use log::info;
use std::boxed::Box;
use std::collections::HashMap;
use std::net::SocketAddr::{self, V4, V6};
use std::pin::Pin;
use std::sync::Arc;
use std::time::{Duration, SystemTime};
use tokio::select;
use tokio::sync::Mutex;
use tokio_stream::Stream;
use tokio_util::sync::CancellationToken;
use tonic::{Request, Response, Status};
use zeroize::Zeroizing;

static PROCESS_TIMEOUT: u64 = 1;

mod pb {
    pub(super) mod mpris {
        tonic::include_proto!("mpris");
    }
    pub(super) mod cakes {
        tonic::include_proto!("cakes");
    }
}

pub use pb::cakes::cakes_server::CakesServer;
pub use pb::mpris::mpris_server::MprisServer;

#[derive(Default)]
pub struct MPRISService {}

#[tonic::async_trait]
impl pb::mpris::mpris_server::Mpris for MPRISService {
    type UpdatesStream = Pin<
        Box<
            dyn Stream<Item = Result<pb::mpris::MprisUpdateReply, tonic::Status>>
                + std::marker::Send,
        >,
    >;

    async fn ping(&self, request: Request<()>) -> Result<Response<()>, Status> {
        println!("Got a request from {:?}", request.remote_addr());
        Ok(Response::new(()))
    }

    async fn updates(
        &self,
        request: tonic::Request<pb::mpris::MprisUpdateRequest>,
    ) -> Result<tonic::Response<Self::UpdatesStream>, tonic::Status> {
        todo!()
    }

    async fn change_player_status(
        &self,
        request: tonic::Request<pb::mpris::ChangePlayerStatusRequest>,
    ) -> Result<tonic::Response<pb::mpris::ChangePlayerStatusReply>, tonic::Status> {
        todo!()
    }

    async fn next(
        &self,
        request: tonic::Request<pb::mpris::NextRequest>,
    ) -> Result<tonic::Response<pb::mpris::NextReply>, tonic::Status> {
        todo!()
    }

    async fn previous(
        &self,
        request: tonic::Request<pb::mpris::PreviousRequest>,
    ) -> Result<tonic::Response<pb::mpris::PreviousReply>, tonic::Status> {
        todo!()
    }

    async fn seek(
        &self,
        request: tonic::Request<pb::mpris::SeekRequest>,
    ) -> Result<tonic::Response<pb::mpris::SeekReply>, tonic::Status> {
        todo!()
    }

    async fn seek_absolute(
        &self,
        request: tonic::Request<pb::mpris::SeekAbsoluteRequest>,
    ) -> Result<tonic::Response<pb::mpris::SeekAbsoluteReply>, tonic::Status> {
        todo!()
    }

    async fn set_position(
        &self,
        request: tonic::Request<pb::mpris::SetPositionRequest>,
    ) -> Result<tonic::Response<pb::mpris::SetPositionReply>, tonic::Status> {
        todo!()
    }
}

macro_rules! abort {
    ($s:expr) => {
        return Err(tonic::Status::aborted($s))
    };
}

macro_rules! precond {
    ($s:expr) => {
        return Err(tonic::Status::failed_precondition($s))
    };
}

macro_rules! eperm {
    ($s:expr) => {
        return Err(tonic::Status::permission_denied($s))
    };
}

macro_rules! unauth {
    ($s:expr) => {
        return Err(tonic::Status::unauthenticated($s))
    };
}

macro_rules! einval {
    ($s:expr) => {
        return Err(tonic::Status::invalid_argument($s))
    };
}

macro_rules! internal {
    ($l: literal, $s:expr) => {
        return Err(tonic::Status::internal(format!($l, $s)))
    };
    ($s:expr) => {
        return Err(tonic::Status::internal($s))
    };
}

fn client_ip(addr: Option<SocketAddr>) -> Result<String, tonic::Status> {
    Ok(match addr {
        None => precond!("no peer address"),
        Some(s) => match s {
            V4(s) => format!("{}", s),
            V6(s) => format!("{}", s),
        },
    })
}

enum AuthState {
    Banned,
    ClientPubkeyReceived(Arc<blindecdh::PublicKey>),
    SharedKeyPendingAcceptance(Zeroizing<Vec<u8>>),
    SharedKeyDeclined,
    SharedKeyAccepted(Zeroizing<Vec<u8>>),
    CertificateIssued,
}

struct AuthStateTracker {
    valid_until: SystemTime,
    state: AuthState,
}

struct PerClientAuthStateTracker(
    HashMap<String, AuthStateTracker>,
    Duration,
    CancellationToken,
);

impl Drop for PerClientAuthStateTracker {
    fn drop(&mut self) {
        self.2.cancel();
        println!("CAKESService dropped");
    }
}

impl PerClientAuthStateTracker {
    /// Create a new per-client auth state tracker.  Steps in the auth
    /// process for a client expire after `step_timeout` duration, so
    /// as to not keep data for a client around for too long.
    fn new(step_timeout: Duration) -> Arc<Mutex<Self>> {
        let auths = HashMap::new();

        let canceller_token = CancellationToken::new();
        let canceller_for_task = canceller_token.clone();

        let ret = Arc::new(Mutex::new(Self(auths, step_timeout, canceller_token)));
        let auths_for_task = ret.clone();

        tokio::task::spawn(async move {
            let mut interval = tokio::time::interval(Duration::from_millis(1000));

            loop {
                select! {
                    _ = interval.tick() => {
                        let mut auths = auths_for_task.lock().await;
                        let expired_keys: Vec<String> = auths.0
                            .iter()
                            .filter(|(_,v)| v.valid_until < SystemTime::now())
                            .map(|(k,_)| k.clone()
                        ).collect();
                        if !expired_keys.is_empty() {
                            info!(target: "CAKESService", "purging authentication states for {} clients", expired_keys.len());
                            for k in expired_keys {
                                auths.0.remove(&k);
                            }
                        }
                        drop(auths);
                    }
                    _ = canceller_for_task.cancelled() => {
                        break;
                    }
                }
            }
        });

        ret
    }

    fn receive_pubkey(
        &mut self,
        client: String,
    ) -> Result<impl FnOnce(blindecdh::PublicKey), tonic::Status> {
        match self.0.get(&client) {
            None => Ok(|pubkey| {
                self.0.insert(
                    client,
                    AuthStateTracker {
                        valid_until: SystemTime::now() + self.1,
                        state: AuthState::ClientPubkeyReceived(Arc::new(pubkey)),
                    },
                );
            }),
            Some(tracker) => match tracker.state {
                AuthState::Banned => eperm!("client banned"),
                _ => abort!("another certificate issuance process is ongoing for this client"),
            },
        }
    }

    fn get_pubkey_and_set_shared_key(
        &mut self,
        client: String,
    ) -> Result<(Arc<blindecdh::PublicKey>, impl FnOnce(Zeroizing<Vec<u8>>)), tonic::Status> {
        let client_pubkey = match self.0.get(&client) {
            Some(AuthStateTracker {
                state: AuthState::ClientPubkeyReceived(client_pubkey),
                ..
            }) => client_pubkey.clone(),
            Some(AuthStateTracker {
                state: AuthState::Banned,
                ..
            }) => eperm!("client banned"),
            None => abort!("client does not have a certificate issuance process ongoing"),
            _ => abort!("another certificate issuance process is ongoing for this client"),
        };
        Ok((client_pubkey, move |shared_key| {
            self.0.insert(
                client,
                AuthStateTracker {
                    valid_until: SystemTime::now() + self.1,
                    state: AuthState::SharedKeyPendingAcceptance(shared_key),
                },
            );
        }))
    }

    fn consume_accepted_shared_secret(
        &mut self,
        client: String,
    ) -> Result<Zeroizing<Vec<u8>>, tonic::Status> {
        match self.0.remove(&client) {
            Some(AuthStateTracker {
                state: AuthState::Banned,
                ..
            }) => eperm!("client banned"),
            Some(AuthStateTracker {
                state: AuthState::SharedKeyPendingAcceptance(_),
                ..
            }) => unauth!("server has not yet accepted the shared key"),
            Some(AuthStateTracker {
                state: AuthState::SharedKeyDeclined,
                ..
            }) => eperm!("the shared key for the client was declined"),
            Some(AuthStateTracker {
                state: AuthState::SharedKeyAccepted(s),
                ..
            }) => Ok(s),
            None => abort!("client does not have a certificate issuance process ongoing"),
            _ => abort!("another certificate issuance process is ongoing for this client"),
        }
    }
}

/// An implementation of the CAKES protocol.
///
/// CAKES stands for Certificate authority kit, ECDH-powered, SAS-authenticated.
/// The protocol facilitates clients obtaining valid certificates from a
/// server running a certificate authority.
///
/// The CAKES server fisrt authenticates a client using a SAS (which relies
/// on both sides visually verifying the communcations haven't been tampered
/// with, like Bluetooth).
///
/// FIXME Once client authentication is successful, issues a certificate to the client.  The
/// client can then use that certificate to contact another service under
/// the same chain of trust used by the certificate, while the service can
/// perform .
///
/// FIXME it is important to spin off the authorization part to a delegate
/// so that the user can interact with this thing.
pub struct CAKESService {
    ongoing_auths: Arc<Mutex<PerClientAuthStateTracker>>,
}

impl CAKESService {
    pub fn new() -> Self {
        Self {
            ongoing_auths: PerClientAuthStateTracker::new(Duration::from_secs(PROCESS_TIMEOUT)),
        }
    }
}

// FIXME move the meat of all this shit to a different struct, leaving the
// protocol wrangling to this interface.  Perhaps a tower::Service would
// be fitting as a way to structure this shit?
#[tonic::async_trait]
impl pb::cakes::cakes_server::Cakes for CAKESService {
    /// This is the first step in CAKES.
    ///
    /// The client has begun a [blindecdh] exchange, and submits its
    /// ephemeral public key through this RPC call.  The response is a
    /// simple Ack, indicating to the client that the key has been
    /// successfully received.
    ///
    /// The server registers the client's pubkey locally in state
    /// [AuthState::ClientPubkeyReceived] for a period of time (defined
    /// when the [CAKESService] was constructed), with the expectation
    /// that the client will later call the next step
    /// ([CAKESServer::server_pubkey]).
    ///
    /// Errors decoding the pubkey will be replied as ABORTs to the client,
    /// and if a client already has another process ongoing, this will
    /// also result in an abort type reply.  If the client is banned,
    /// a PERMISSION_ERROR gRPC reply is returned.
    async fn client_pubkey(
        &self,
        request: tonic::Request<pb::cakes::EcdhKey>,
    ) -> std::result::Result<tonic::Response<pb::cakes::Ack>, tonic::Status> {
        let remote_ip = client_ip(request.remote_addr())?;
        let mut auths = self.ongoing_auths.lock().await;
        let setter = auths.receive_pubkey(remote_ip.clone())?;

        let pubkey = match blindecdh::PublicKey::from_pem(&request.get_ref().pubkey) {
            Ok(p) => p,
            Err(e) => einval!(format!("unusable public key PEM: {}", e)),
        };

        setter(pubkey);
        info!(target: "CAKESService", "client pubkey submitted by {} registered", remote_ip);
        Ok(pb::cakes::Ack {}.into())
    }

    /// This is the second step in CAKES.
    ///
    /// In this step, the client requests the public key of the server in order to
    /// complete the ECDH shared key derivation on the client side.  Before this can
    /// succeed, the client's own public key must have been submitted in the prior
    /// step, thus stored locally as state [AuthState::ClientPubkeyReceived].
    ///
    /// Simultaneously, the server (this code), now in possession of the client's
    /// public key, generates its own private key for the exchange, and derives the
    /// shared key on its own using them.
    ///
    /// If everything succeeds, the server replies with its own public key (used for
    /// this ECDH exchange) and stores the derived key in pending state
    /// ([AuthState::SharedKeyPendingAcceptance]).
    ///
    /// From then on, it is the responsibility of other code to move that key from
    /// pending acceptance to accepted, so the next step (calling
    /// [CAKESService::issue_certificate] can be performed by the client.
    ///
    /// Clients which have not submitted their client public key, or were in a
    /// different step of the process, receive ABORT gRPC replies.  Banned clients
    /// get a PERMISSION_DENIED gRPC reply.
    async fn server_pubkey(
        &self,
        request: tonic::Request<pb::cakes::Ack>,
    ) -> std::result::Result<tonic::Response<pb::cakes::EcdhKey>, tonic::Status> {
        let remote_ip = client_ip(request.remote_addr())?;
        let mut auths = self.ongoing_auths.lock().await;
        let (client_pubkey, setter) = auths.get_pubkey_and_set_shared_key(remote_ip.clone())?;

        let (privkey, pubkey_pem) =
            match blindecdh::PrivateKey::generate(match blindecdh::curve_from_name("secp256k1") {
                Ok(curve) => curve,
                Err(e) => internal!("could not find expected curve secp256k1: {}"),
            }) {
                Ok(key) => {
                    let pubkey_pem = match key.public_key() {
                        Ok(key) => match key.pem() {
                            Ok(pem) => pem,
                            Err(e) => internal!("could not serialize public key: {}", e),
                        },
                        Err(e) => internal!("could not derive public key: {}", e),
                    };
                    (key, pubkey_pem)
                }
                Err(e) => internal!("could not generate private key: {}", e),
            };

        info!(target: "CAKESService", "server pubkey requested by {}", remote_ip);

        let shared_key = match blindecdh::Agreement::try_from((privkey, &*client_pubkey)) {
            Ok(k) => match k.derive(ring::hkdf::HKDF_SHA256, None, None, None) {
                Ok(k) => k,
                Err(e) => internal!("could not derive shared key: {}", e),
            },
            Err(e) => internal!("could not create ECDH: {}", e),
        };

        info!(target: "CAKESService", "shared key for {} has been derived and is pending acceptance from user", remote_ip);
        setter(shared_key);

        Ok(pb::cakes::EcdhKey { pubkey: pubkey_pem }.into())
    }

    /// This is the third step in CAKES.
    ///
    /// This step issues a certificate to an authorized client.
    ///
    /// A client is authorized to obtain a certificate if:
    ///
    /// 1. It has completed the ECDH phase (ClientPubkey + ServerPubkey),
    ///    and therefore its shared key appeared at some point in the
    ///    `ongoing_auths` struct member as state
    ///    [AuthState::SharedKeyPendingAcceptance].
    /// 2. Another part of the system has transitioned said shared key
    ///    to [AuthState::SharedKeyAccepted].
    ///
    /// The client must call this with an encrypted payload.  The payload
    /// must contain its certificate request, and must be encrypted with the
    /// shared key it derived on its side (which should match the key we have).
    ///
    /// A successful reply to authenticated clients consists of a two-part message:
    ///
    /// 1. The issued certificate.  A newly-issued certificate, signed by
    ///    the FIXME `pskca.CA` object, which the caller may use to do mutual TLS
    ///    via gRPC or mTLS to any services protected by certificates signed
    ///    by the CA (or the CA certificate itself).
    /// 2. A chain of trust (derived from the FIXME `pskca.CA` object) to be sent
    ///    to the client for purposes of authenticating against the
    ///    authenticated service that the issued certificate is for.
    ///
    /// A reply sent to a client whose key has not yet been accepted involve the
    /// gRPC status code UNAUTHENTICATED.  A client in such a predicament should
    /// simply retry the same request in a few seconds.
    ///
    /// Replies to clients who were declined involve the gRPC status code
    /// PERMISSION_DENIED.
    ///
    /// Replies to clients who did not follow the process correctly, or who
    /// do not possess the correct shared key (which means their payload cannot
    /// be decrypted) involve the gRPC status code ABORTED.
    async fn issue_certificate(
        &self,
        request: tonic::Request<pb::cakes::IssueCertificateRequest>,
    ) -> std::result::Result<tonic::Response<pb::cakes::IssueCertificateReply>, tonic::Status> {
        let remote_ip = client_ip(request.remote_addr())?;
        let mut auths = self.ongoing_auths.lock().await;
        let shared_secret = auths.consume_accepted_shared_secret(remote_ip)?;

        todo!()
    }
}
