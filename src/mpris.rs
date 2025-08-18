use async_stream::{stream, try_stream};
use derivative::Derivative;
use futures_util::{Stream, stream::StreamExt};
use log::{debug, error, warn};
use std::cmp::max;
use std::collections::HashMap;
use std::error::Error;
use std::fmt;
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};
use std::time::Duration;
use std::time::SystemTime;
use tokio::select;
use tokio::time::timeout;
use zbus::{proxy, zvariant};

static PLAYER_INTERFACE: &str = "org.mpris.MediaPlayer2.Player";
static APP_INTERFACE: &str = "org.mpris.MediaPlayer2";
// Poll for properties every X seconds for players whose Seeked() signal does not work.
static SEEK_POLL_FREQUENCY: u64 = 1;
// Two hundred microseconds of slack when polling for differences.
static SEEK_DELTA_SLACK: u64 = 200000;

fn is_mpris(bus_name: &str) -> bool {
    bus_name.starts_with(APP_INTERFACE)
}

fn position_must_be_polled(props: &MediaPlayerInfo) -> bool {
    props.app_properties.DesktopEntry == Some("org.gnome.Totem".to_string())
}

// FIXME remove all expects and unwraps

#[proxy(
    default_service = "org.freedesktop.DBus",
    default_path = "/org/freedesktop/DBus",
    interface = "org.freedesktop.DBus"
)]
trait DBusClientNameMonitor {
    #[zbus(signal)]
    fn name_owner_changed(
        &self,
        bus_name: String,
        old_owner: String,
        new_owner: String,
    ) -> zbus::Result<()>;

    fn list_names(&self) -> zbus::Result<Vec<String>>;

    fn get_name_owner(&self, bus_name: String) -> zbus::Result<String>;
}

#[derive(Debug)]
enum MediaPlayerConnectionEvent {
    PlayerAppeared { owner: String },
    PlayerGone { owner: String },
}

struct MediaPlayerConnectionMonitor {
    listing: Pin<Box<Vec<String>>>,
    change_monitor: NameOwnerChangedStream,
}

impl MediaPlayerConnectionMonitor {
    async fn new(connection: Arc<zbus::Connection>) -> zbus::Result<Self> {
        let proxy = Box::pin(DBusClientNameMonitorProxy::new(&connection).await?);
        let media_player_changes = proxy.receive_name_owner_changed().await?;
        let mut listing: Vec<String> = vec![];
        for bus_name in proxy.list_names().await?.iter() {
            if is_mpris(bus_name) {
                let owner = proxy.get_name_owner(bus_name.clone()).await?;
                debug!(target: "MediaPlayerConnectionMonitor", "discovered preexisting player: bus_name={} owner={}", bus_name, owner);
                listing.push(owner.clone())
            }
        }
        Ok(Self {
            listing: Box::pin(listing),
            change_monitor: media_player_changes,
        })
    }
}

impl Stream for MediaPlayerConnectionMonitor {
    type Item = zbus::Result<MediaPlayerConnectionEvent>;

    fn poll_next(
        mut self: Pin<&mut MediaPlayerConnectionMonitor>,
        cx: &mut Context<'_>,
    ) -> Poll<Option<Self::Item>> {
        let target = "MediaPlayerConnectionMonitor::NameOwnerChanged";

        while !self.listing.is_empty() {
            let owner = self.listing.pop().unwrap();
            return Poll::Ready(Some(Ok(MediaPlayerConnectionEvent::PlayerAppeared {
                owner,
            })));
        }
        loop {
            match Pin::new(&mut self.change_monitor).poll_next(cx) {
                Poll::Ready(Some(msg)) => {
                    let args: NameOwnerChangedArgs = match msg.args() {
                        Ok(args) => args,
                        Err(zbus_error) => return Poll::Ready(Some(Err(zbus_error))),
                    };
                    if !is_mpris(&args.bus_name) {
                        continue;
                    }
                    if args.new_owner == "" {
                        debug!(
                            target: target,
                            "player gone: bus_name={} old_owner={}",
                            args.bus_name, args.old_owner
                        );
                        return Poll::Ready(Some(Ok(MediaPlayerConnectionEvent::PlayerGone {
                            owner: args.old_owner.clone(),
                        })));
                    } else if args.old_owner == "" {
                        debug!(
                            target: target,
                            "player appeared: bus_name={} new_owner={}",
                            args.bus_name, args.new_owner
                        );
                        return Poll::Ready(Some(Ok(MediaPlayerConnectionEvent::PlayerAppeared {
                            owner: args.new_owner.clone(),
                        })));
                    } else {
                        warn!(
                            target: target,
                            "player changed: bus_name={} old_owner={} new_owner={}",
                            args.bus_name, args.old_owner, args.new_owner
                        )
                    }
                }
                Poll::Ready(_) => return Poll::Ready(None),
                Poll::Pending => return Poll::Pending,
            }
        }
    }
}

type DBusPropertyBag = HashMap<String, zvariant::OwnedValue>;

#[proxy]
trait DBusProperties {
    #[zbus(signal)]
    fn properties_changed(
        &self,
        interface: String,
        changed_properties: DBusPropertyBag,
        invalidated_properties: Vec<String>,
    ) -> zbus::Result<()>;

    fn get_all(&self, interface_name: &str) -> zbus::Result<DBusPropertyBag>;
}

#[derive(Debug, Clone, Default, PartialEq)]
pub enum LoopStatus {
    #[default]
    None,
    Track,
    Playlist,
}

impl TryFrom<&str> for LoopStatus {
    type Error = String;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        Ok(match value {
            "None" => Self::None,
            "Track" => Self::Track,
            "Playlist" => Self::Playlist,
            _ => {
                return Err(format!("invalid LoopStatus value {}", value));
            }
        })
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum PlaybackStatus {
    Stopped,
    Paused,
    Playing,
}

impl TryFrom<&str> for PlaybackStatus {
    type Error = String;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        Ok(match value {
            "Stopped" => Self::Stopped,
            "Paused" => Self::Paused,
            "Playing" => Self::Playing,
            _ => {
                return Err(format!("invalid PlaybackStatus value {}", value));
            }
        })
    }
}

#[derive(Debug)]
pub enum PropertiesConversionError {
    WrongTypeForProperty(String, zvariant::Error),
    MissingProperty(String),
    InvalidValueForProperty(String),
}

impl From<(&str, zvariant::Error)> for PropertiesConversionError {
    fn from(e: (&str, zvariant::Error)) -> Self {
        Self::WrongTypeForProperty(e.0.to_string(), e.1)
    }
}

impl From<&str> for PropertiesConversionError {
    fn from(e: &str) -> Self {
        Self::MissingProperty(e.to_string())
    }
}

impl fmt::Display for PropertiesConversionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MissingProperty(v) => write!(f, "missing expected property {v}"),
            Self::WrongTypeForProperty(v, e) => write!(f, "incorrect type for property {v}: {e}"),
            Self::InvalidValueForProperty(errmsg) => write!(f, "{errmsg}"),
        }
    }
}

impl Error for PropertiesConversionError {}

#[derive(Debug, Clone, Derivative)]
#[derivative(PartialEq)]
#[allow(non_snake_case)]
pub struct MediaPlayer2Properties {
    pub CanQuit: bool,
    pub Fullscreen: bool,
    pub CanSetFullscreen: bool,
    pub CanRaise: bool,
    pub HasTrackList: bool,
    pub Identity: String,
    pub DesktopEntry: Option<String>,
    #[derivative(PartialEq = "ignore")]
    pub LastUpdated: SystemTime,
}

impl TryFrom<(&DBusPropertyBag, SystemTime)> for MediaPlayer2Properties {
    type Error = PropertiesConversionError;

    fn try_from((s, t): (&DBusPropertyBag, SystemTime)) -> Result<Self, Self::Error> {
        Ok(MediaPlayer2Properties {
            LastUpdated: t,
            CanQuit: {
                match s.get("CanQuit") {
                    Some(v) => <bool>::try_from(v).map_err(|m| ("CanQuit", m))?,
                    None => false,
                }
            },
            Fullscreen: {
                match s.get("Fullscreen") {
                    Some(v) => <bool>::try_from(v).map_err(|m| ("Fullscreen", m))?,
                    None => false,
                }
            },
            CanSetFullscreen: {
                match s.get("CanSetFullscreen") {
                    Some(v) => <bool>::try_from(v).map_err(|m| ("CanSetFullscreen", m))?,
                    None => false,
                }
            },
            CanRaise: {
                match s.get("CanRaise") {
                    Some(v) => <bool>::try_from(v).map_err(|m| ("CanRaise", m))?,
                    None => false,
                }
            },
            HasTrackList: {
                match s.get("HasTrackList") {
                    Some(v) => <bool>::try_from(v).map_err(|m| ("HasTrackList", m))?,
                    None => false,
                }
            },
            Identity: {
                let val = s.get("Identity").ok_or("Identity")?;
                <String>::try_from(val.clone()).map_err(|m| ("Identity", m))?
            },
            DesktopEntry: match s.get("DesktopEntry") {
                None => None,
                Some(val) => {
                    Some(<String>::try_from(val.clone()).map_err(|m| ("DesktopEntry", m))?)
                }
            },
        })
    }
}

#[derive(Debug, Clone, Derivative)]
#[derivative(PartialEq)]
#[allow(non_snake_case)]
pub struct MediaPlayer2PlayerProperties {
    pub CanControl: bool,
    pub CanGoNext: bool,
    pub CanGoPrevious: bool,
    pub CanPause: bool,
    pub CanPlay: bool,
    pub CanSeek: bool,
    pub LoopStatus: LoopStatus,
    pub MaximumRate: f64,
    pub MinimumRate: f64,
    pub Rate: f64,
    pub PlaybackStatus: PlaybackStatus,
    /// Position in microseconds.
    pub Position: i64,
    pub Length: Option<i64>,
    pub Shuffle: Option<bool>,
    pub Metadata: HashMap<String, zvariant::OwnedValue>,
    #[derivative(PartialEq = "ignore")]
    pub LastUpdated: SystemTime,
}

impl TryFrom<(&DBusPropertyBag, SystemTime)> for MediaPlayer2PlayerProperties {
    type Error = PropertiesConversionError;

    fn try_from((s, t): (&DBusPropertyBag, SystemTime)) -> Result<Self, Self::Error> {
        let metadata_variants = s.get("Metadata").ok_or("Metadata")?;

        let metadata_static =
            <HashMap<String, zvariant::Value<'static>>>::try_from(metadata_variants.clone())
                .map_err(|m| ("Metadata", m))?;
        let metadata: HashMap<String, zvariant::OwnedValue> = metadata_static
            .iter()
            .map(|(k, v)| (k.clone(), v.try_to_owned().unwrap()))
            .collect();

        Ok(MediaPlayer2PlayerProperties {
            LastUpdated: t,
            CanControl: <bool>::try_from(s.get("CanControl").cloned().ok_or("CanControl")?)
                .map_err(|m| ("CanControl", m))?,
            CanGoNext: <bool>::try_from(s.get("CanGoNext").ok_or("CanGoNext")?)
                .map_err(|m| ("CanGoNext", m))?,
            CanGoPrevious: <bool>::try_from(s.get("CanGoPrevious").ok_or("CanGoPrevious")?)
                .map_err(|m| ("CanGoPrevious", m))?,
            CanPause: <bool>::try_from(s.get("CanPause").ok_or("CanPause")?)
                .map_err(|m| ("CanPause", m))?,
            CanPlay: <bool>::try_from(s.get("CanPlay").ok_or("CanPlay")?)
                .map_err(|m| ("CanPlay", m))?,
            CanSeek: <bool>::try_from(s.get("CanSeek").ok_or("CanSeek")?)
                .map_err(|m| ("CanSeek", m))?,
            LoopStatus: match s.get("LoopStatus") {
                None => LoopStatus::default(),
                Some(val) => {
                    let downcasted =
                        <String>::try_from(val.clone()).map_err(|m| ("LoopStatus", m))?;
                    LoopStatus::try_from(downcasted.as_str())
                        .map_err(|e| Self::Error::InvalidValueForProperty(e))?
                }
            },
            MaximumRate: <f64>::try_from(s.get("MaximumRate").ok_or("MaximumRate")?)
                .map_err(|m| ("MaximumRate", m))?,
            MinimumRate: <f64>::try_from(s.get("MinimumRate").ok_or("MinimumRate")?)
                .map_err(|m| ("MinimumRate", m))?,
            Rate: {
                let rate =
                    <f64>::try_from(s.get("Rate").ok_or("Rate")?).map_err(|m| ("Rate", m))?;
                if rate < 0.0001 {
                    warn!("player reported invalid rate {}, clamping to 1.0", rate);
                    1.0
                } else {
                    rate
                }
            },
            PlaybackStatus: {
                let val = s.get("PlaybackStatus").ok_or("PlaybackStatus")?;
                let downcasted =
                    <String>::try_from(val.clone()).map_err(|m| ("PlaybackStatus", m))?;
                PlaybackStatus::try_from(downcasted.as_str())
                    .map_err(|e| Self::Error::InvalidValueForProperty(e))?
            },
            // Position in milliseconds.
            Position: <i64>::try_from(s.get("Position").ok_or("Position")?)
                .map_err(|m| ("Position", m))?,
            Length: {
                match metadata.get("mpris:length") {
                    None => None,
                    Some(length_variant) => {
                        let m = <i64>::try_from(length_variant.clone())
                            .map_err(|m| ("mpris:length in metadata", m))?;
                        Some(m)
                    }
                }
            },
            Shuffle: match s.get("Shuffle") {
                None => None,
                Some(v) => Some(<bool>::try_from(v.clone()).map_err(|m| ("Shuffle", m))?),
            },
            Metadata: metadata,
        })
    }
}

#[derive(Debug, Clone)]
#[allow(non_snake_case)]
struct MediaPlayer2PlayerPositionRate {
    // Position in microseconds.
    Position: i64,
    Rate: f64,
}

impl TryFrom<&DBusPropertyBag> for MediaPlayer2PlayerPositionRate {
    type Error = PropertiesConversionError;

    fn try_from(s: &DBusPropertyBag) -> Result<Self, Self::Error> {
        Ok(MediaPlayer2PlayerPositionRate {
            // Position in milliseconds.
            Position: <i64>::try_from(s.get("Position").ok_or("Position")?)
                .map_err(|m| ("Position", m))?,
            Rate: {
                let rate =
                    <f64>::try_from(s.get("Rate").ok_or("Rate")?).map_err(|m| ("Rate", m))?;
                if rate < 0.0001 {
                    warn!("player reported invalid rate {}, clamping to 1.0", rate);
                    1.0
                } else {
                    rate
                }
            },
        })
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct MediaPlayerInfo {
    pub app_properties: MediaPlayer2Properties,
    pub player_properties: MediaPlayer2PlayerProperties,
}

fn merge_properties(
    invalidated_properties: Vec<String>,
    changed_properties: DBusPropertyBag,
    current_props: &mut DBusPropertyBag,
) {
    for k in invalidated_properties.iter() {
        debug!(target: "merge_properties", "invalidating property {k}");
        current_props.remove(k);
    }
    for (k, v) in changed_properties.into_iter() {
        if current_props.get(&k) != Some(&v) {
            debug!(target: "merge_properties", "updating property {}:\nfrom {:#?}\nto{:#?}", k, current_props.get(&k), v);
            current_props.insert(k, v);
        }
    }
}

// fn diff_properties(a: &DBusPropertyBag, b: &DBusPropertyBag) {
//     let propnames: HashSet<String> = HashSet::from_iter(
//         itertools::concat(vec![
//             a.iter().map(|(k, _)| k.clone()).collect::<Vec<_>>(),
//             b.iter().map(|(k, _)| k.clone()).collect::<Vec<_>>(),
//         ])
//         .iter()
//         .cloned(),
//     );
//     let mut diff = false;
//     for prop in propnames {
//         match (a.get(&prop), b.get(&prop)) {
//             (Some(va), Some(vb)) => {
//                 if va != vb {
//                     debug!("property {prop} differs      a: {:?}    b: {:?}", va, vb);
//                     diff = true;
//                 }
//             }
//             (Some(_), None) => {
//                 debug!("only first has property {prop}");
//                 diff = true
//             }
//             (None, Some(_)) => {
//                 debug!("only secnd has property {prop}");
//                 diff = true
//             }
//             (None, None) => (),
//         }
//     }
//     if !diff {
//         debug!("no differences in a / b");
//     }
// }

#[proxy(
    default_path = "/org/mpris/MediaPlayer2",
    interface = "org.mpris.MediaPlayer2.Player", // FIXME const static above
)]
trait MPRISPlayer {
    #[zbus(signal)]
    fn seeked(&self, pos: i64) -> zbus::Result<()>;
}

#[derive(Debug)]
pub enum MediaPlayerMonitorError {
    ConversionError(PropertiesConversionError),
    ZbusError(zbus::Error),
}

impl From<zbus::Error> for MediaPlayerMonitorError {
    fn from(s: zbus::Error) -> Self {
        Self::ZbusError(s)
    }
}

impl From<PropertiesConversionError> for MediaPlayerMonitorError {
    fn from(s: PropertiesConversionError) -> Self {
        Self::ConversionError(s)
    }
}

impl fmt::Display for MediaPlayerMonitorError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ConversionError(v) => write!(f, "conversion error: {v}"),
            Self::ZbusError(e) => write!(f, "D-Bus error: {e}"),
        }
    }
}

impl Error for MediaPlayerMonitorError {}

struct MediaPlayerMonitor<'a> {
    owner: String,
    app_proxy: DBusPropertiesProxy<'a>,
    player_proxy: MPRISPlayerProxy<'a>,
}

impl<'a> MediaPlayerMonitor<'_> {
    async fn new(
        connection: Arc<zbus::Connection>,
        owner: String,
    ) -> Result<Self, MediaPlayerMonitorError> {
        let player_proxy = DBusPropertiesProxy::builder(&connection)
            .destination(owner.clone())?
            .path("/org/mpris/MediaPlayer2")?
            .interface("org.freedesktop.DBus.Properties")?
            .build()
            .await?;

        let seeked_monitor_proxy = MPRISPlayerProxy::builder(&connection)
            .destination(owner.clone())?
            .build()
            .await?;

        Ok(Self {
            app_proxy: player_proxy,
            player_proxy: seeked_monitor_proxy,
            owner,
        })
    }

    fn stream(self) -> impl Stream<Item = Result<MediaPlayerInfo, MediaPlayerMonitorError>> {
        try_stream! {
            let owner = self.owner.clone();
            let target = format!("MediaPlayerMonitor::{owner}");

            let mut player_props_changed = self.app_proxy.receive_properties_changed().await?;
            let mut player_seeked = self.player_proxy.receive_seeked().await?;

            debug!(target: target.as_str(), "about to monitor for properties");

            let mut current_app_props: DBusPropertyBag = self
                .app_proxy.get_all(APP_INTERFACE)
                .await?;
            debug!(target: target.as_str(), "first set of properties from app: {:#?}", current_app_props);

            let mut current_player_props: DBusPropertyBag = self
                .app_proxy
                .get_all(PLAYER_INTERFACE)
                .await?;
            debug!(target: target.as_str(), "first set of properties from player: {:#?}", current_player_props);

            let now = SystemTime::now();
            let mut props = MediaPlayerInfo{
                app_properties: TryInto::<MediaPlayer2Properties>::try_into((&current_app_props, now))?,
                player_properties: TryInto::<MediaPlayer2PlayerProperties>::try_into((&current_player_props, now))?,
            };
            yield props.clone();

            let seeked_timeout = if position_must_be_polled(&props) {
                std::time::Duration::from_millis(SEEK_POLL_FREQUENCY * 1000)
            } else {
                std::time::Duration::MAX
            };

            enum Requery {
                AppProps,
                PlayerProps,
                Position,
            }

            // Some players emit this signal multiple times to change
            // properties one by one.  Loop to detect this, and coalesce
            // updates.
            macro_rules! spin {
                () => {
                    loop {
                        select! {
                            v = timeout(Duration::from_millis(50), player_props_changed.next()) => {
                                match v {
                                    Err(_) => break,
                                    Ok(_) => continue,
                                }
                            }
                            v = timeout(Duration::from_millis(50), player_seeked.next()) => {
                                match v {
                                    Err(_) => break,
                                    Ok(_) => continue,
                                }
                            }
                        }
                    }
                }
            }

            loop {
                match select! {
                    maybe_propchangemsg = player_props_changed.next() => {
                        match maybe_propchangemsg {
                            None => break,
                            Some(propchangemsg) => {
                                match propchangemsg.args() {
                                    Ok(updated) => {
                                        if updated.interface == PLAYER_INTERFACE {
                                            debug!(target: target.as_str(), "new properties received: {:?}", updated);
                                            merge_properties(
                                                updated.invalidated_properties,
                                                updated.changed_properties,
                                                &mut current_player_props,
                                            );
                                            spin!();
                                            Ok(Requery::PlayerProps)
                                        } else if updated.interface == APP_INTERFACE {
                                            debug!(target: target.as_str(), "new properties received: {:?}", updated);
                                            merge_properties(
                                                updated.invalidated_properties,
                                                updated.changed_properties,
                                                &mut current_app_props,
                                            );
                                            Ok(Requery::AppProps)
                                        } else {
                                            warn!(target: target.as_str(), "interface {} not supported: {:?}", updated.interface, updated);
                                            continue;
                                        }
                                    }
                                    Err(e) => Err(e),
                                }
                            }
                        }
                    }
                    maybe_seekedmsg = timeout(seeked_timeout, player_seeked.next()) => {
                        match maybe_seekedmsg {
                            Ok(None) => break,
                            Err(_)=> {
                                // Player does not emit seeked signals, and we have reached
                                // a timeout to check if the expected time is the predicted time.
                                Ok(Requery::Position)
                            }
                            Ok(Some(seekedmsg)) => {
                                match seekedmsg.args() {
                                    Ok(seeked_args) => {
                                        debug!(target: target.as_str(), "player seeked: {:?}", seeked_args);
                                        let mut posprop: DBusPropertyBag = HashMap::new();
                                        posprop.insert("Position".to_string(), zvariant::Value::new(seeked_args.pos).try_to_owned().unwrap());
                                        merge_properties(
                                            vec![],
                                            posprop,
                                            &mut current_player_props,
                                        );
                                        spin!();
                                        Ok(Requery::PlayerProps)
                                    }
                                    Err(e) => Err(e),
                                }
                            }
                        }
                    }
                }? {
                    Requery::AppProps => {
                        debug!(target: target.as_str(), "retrieving all app properties");
                        let actual_properties: DBusPropertyBag = self
                            .app_proxy
                            .get_all(APP_INTERFACE)
                            .await?;
                        merge_properties(vec![], actual_properties, &mut current_app_props);
                    }
                    Requery::PlayerProps => {
                        debug!(target: target.as_str(), "retrieving all player properties");
                        let actual_properties: DBusPropertyBag = self
                            .app_proxy
                            .get_all(PLAYER_INTERFACE)
                            .await?;
                        merge_properties(vec![], actual_properties, &mut current_player_props);
                    }
                    Requery::Position => {
                        if props.player_properties.PlaybackStatus == PlaybackStatus::Playing {
                            // Must query all properties, regrettably, because Totem's
                            // Position.Get() method returns a perennially-out-of-date value.
                            let actual_properties = self
                                .app_proxy
                                .get_all(PLAYER_INTERFACE)
                                .await?;
                            let curr_pos_rate = TryInto::<MediaPlayer2PlayerPositionRate>::try_into(&actual_properties)?;
                            let now = SystemTime::now();
                            let position_difference = max(0, curr_pos_rate.Position - props.player_properties.Position) as u64;
                            let predicted = (
                                (
                                    {
                                        let elapsed = now.duration_since(props.player_properties.LastUpdated).unwrap_or(Duration::from_secs(0));
                                        elapsed.as_secs() * 1000000 + u64::from(elapsed.subsec_micros())
                                    } as f64
                                ) * curr_pos_rate.Rate
                            ) as u64;
                            // warn!("Difference {}", position_difference);
                            // warn!("Predicted  {}", predicted);
                            if !(predicted - SEEK_DELTA_SLACK <= position_difference && position_difference <= predicted + SEEK_DELTA_SLACK) {
                                debug!(target: target.as_str(), "position outside tolerance, updating properties");
                                merge_properties(vec![], actual_properties, &mut current_player_props);
                            }
                        }
                    }
                }

                let now = SystemTime::now();
                let latest_props = MediaPlayerInfo{
                    app_properties: TryInto::<MediaPlayer2Properties>::try_into((&current_app_props, now))?,
                    player_properties: TryInto::<MediaPlayer2PlayerProperties>::try_into((&current_player_props, now))?,
                };

                if props != latest_props {
                    debug!(target: target.as_str(), "properties changed, emitting");
                    yield latest_props.clone();
                    props = latest_props;
                }
            };

            debug!(target: target.as_str(), "no more properties changed")
        }
    }
}

#[derive(Debug)]
pub enum MediaPlayerEvent {
    PlayerAppeared {
        owner: String,
        properties: MediaPlayerInfo,
    },
    PlayerPropertiesChanged {
        owner: String,
        properties: MediaPlayerInfo,
    },
    PlayerGone {
        owner: String,
        error: Option<MediaPlayerMonitorError>,
    },
}

type MediaPlayerStreamMap =
    HashMap<String, Pin<Box<dyn Stream<Item = Result<MediaPlayerInfo, MediaPlayerMonitorError>>>>>;

pub struct MediaPlayerCollection {
    connection: Arc<zbus::Connection>,
    connection_monitor: MediaPlayerConnectionMonitor,
    announcements_tracker: HashMap<String, ()>,
    monitors: MediaPlayerStreamMap,
}

impl MediaPlayerCollection {
    pub async fn new(connection: Arc<zbus::Connection>) -> zbus::Result<Self> {
        let connection_monitor = MediaPlayerConnectionMonitor::new(connection.clone())
            .await
            .expect("Must have succeeded in querying");

        // let canceller = CancellationToken::new();

        Ok(Self {
            connection: connection.clone(),
            connection_monitor,
            announcements_tracker: HashMap::new(),
            monitors: HashMap::new(),
        })
    }

    pub fn stream(
        &mut self,
    ) -> impl Stream<Item = Result<MediaPlayerEvent, MediaPlayerMonitorError>> {
        let target = "MediaPlayerCollection"; // FIXME fix targets everywhere

        fn with_id<S: Stream>(id: String, stream: S) -> impl Stream<Item = (String, S::Item)> {
            stream.map(move |item| (id.clone(), item))
        }

        stream! {
            debug!(target: target, "begun");
            loop {
                let player_stream_list: Vec<_> = self.monitors
                    .iter_mut()
                    .map(|(k, v)| with_id(k.clone(), v))
                    .collect();
                let mut player_streams = futures_util::stream::select_all(player_stream_list);

                match select! {
                    prp = player_streams.next(), if !player_streams.is_empty() => {
                        drop(player_streams);
                        match prp {
                            None => {
                                panic!("a player is no longer producing streams, this should never happen");
                            }
                            Some((owner, Err(e))) => {
                                match &e
                                {
                                    MediaPlayerMonitorError::ZbusError(zbus::Error::MethodError(ownederrorname, x, y)) => {
                                        if *ownederrorname == "org.freedesktop.DBus.Error.ServiceUnknown" {
                                            error!(target: target, "removing player {} because it was gone while talking to player ({:?} {})", owner, x, y);
                                        } else {
                                            error!(target: target, "removing player {} because it errored: {:?}", owner, e)
                                        }
                                    }
                                    _ => {
                                        error!(target: target, "removing player {} because it errored: {:?}", owner, e)
                                    }
                                };
                                self.monitors.remove(&owner);
                                self.announcements_tracker.remove(&owner);
                                yield Ok(MediaPlayerEvent::PlayerGone{owner, error: Some(e)});
                                Ok(())
                            }
                            Some((owner, Ok(new_props))) => {
                                if let Some(_) = self.announcements_tracker.get(&owner) {
                                    debug!(target: target, "player {} produced new props", owner);
                                    yield Ok(MediaPlayerEvent::PlayerPropertiesChanged {owner: owner, properties: new_props })
                                } else {
                                    debug!(target: target, "player {} confirmed appearance with new props", owner);
                                    self.announcements_tracker.insert(owner.clone(), ());
                                    yield Ok(MediaPlayerEvent::PlayerAppeared {owner: owner, properties: new_props})
                                };
                                Ok(())
                            }
                        }
                    }
                    mon = self.connection_monitor.next() => {
                        drop(player_streams);
                        match mon {
                            None => break,
                            Some(msg) => match msg {
                                Ok(MediaPlayerConnectionEvent::PlayerAppeared { owner }) => {
                                    debug!(target: target, "player {} appeared", owner);
                                    match MediaPlayerMonitor::new(self.connection.clone(), owner.clone()).await {
                                        Ok(player_monitor) => {
                                            let player_prop_stream = player_monitor.stream();
                                            self.monitors.insert(owner, Box::pin(player_prop_stream));
                                            Ok(())
                                        }
                                        Err(e) => {
                                            error!(target: target, "could not create monitor for player {}: {:?}", owner, e);
                                            Err(e)
                                        }
                                    }
                                }
                                Ok(MediaPlayerConnectionEvent::PlayerGone { owner }) => {
                                    debug!(target: target, "player {} gone, removing", owner);
                                    self.monitors.remove(&owner);
                                    self.announcements_tracker.remove(&owner);
                                    yield Ok(MediaPlayerEvent::PlayerGone{owner, error: None});
                                    Ok(())
                                }
                                Err(e) => {
                                    error!(target: target, "connection monitor failed: {:?}", e);
                                    Err(e.into())
                                }
                            }
                        }
                    },
                } {
                    Ok(()) => (),
                    Err(e) => {
                        yield Err(e);
                        break;
                    }
                };
            }
            debug!(target: target, "ended")
        }
    }
}
