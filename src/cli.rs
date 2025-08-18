extern crate hassmpris_agent;

use hassmpris_agent::mpris;

use futures_util::pin_mut;
use futures_util::stream::StreamExt;
use std::sync::Arc;
use zbus::Connection;

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    env_logger::builder()
        .format_timestamp(Some(env_logger::TimestampPrecision::Millis))
        .init();

    let mut player_collection = mpris::MediaPlayerCollection::new(Arc::new(
        Connection::session().await.map_err(|e| Box::new(e))?,
    ))
    .await
    .map_err(|e| Box::new(e))?;

    let stream = player_collection.stream();
    pin_mut!(stream);

    let mut last_error: Result<(), Box<dyn std::error::Error>> = Ok(());
    while let Some(event) = stream.next().await {
        match event {
            Ok(event) => {
                println!("Event: {:#?}", event);
                last_error = Ok(())
            }
            Err(e) => {
                eprintln!("Error: {:#?}", e);
                last_error = Err(Box::new(e))
            }
        }
    }
    last_error
}
