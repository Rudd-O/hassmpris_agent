use std::{path::PathBuf, str::FromStr, time::Duration};

use clap::{Parser, arg, command};
use env_logger;
use hassmpris_agent::grpc;
use log::info;
use microxdg::{Xdg, XdgError};
use tokio::{fs::read, select};
use tonic::transport::{Certificate, Identity, Server, ServerTlsConfig};

const TLS_HANDSHAKE_TIMEOUT: u64 = 5;
const REQUEST_TIMEOUT: u64 = 30;

#[derive(Parser, Debug)]
#[command(version, about, long_about = None)]
struct Args {
    /// Path to the PEM-encoded certificate file.  If unspecified, use
    /// (or create) server.crt under ~/.config/hassmpris.
    #[arg(long)]
    certificate_file: Option<String>,

    /// Path to the PEM-encoded key file.  If unspecified, use (or create)
    /// server.key under ~/.config/hassmpris.
    #[arg(long)]
    key_file: Option<String>,

    /// Path to the PEM-encoded CA certificate file.  If unspecified, use
    /// (or create) ca.cert under ~/.config/hassmpris.
    #[arg(long)]
    ca_certificate_file: Option<String>,

    /// Path to the PEM-encoded CA key file.  If unspecified, use
    /// (or create) ca.key under ~/.config/hassmpris.
    #[arg(long)]
    ca_key_file: Option<String>,
}

fn xdg_config_dir_for_app() -> Result<PathBuf, XdgError> {
    let xdg = Xdg::new()?;
    let config_dir = xdg.config()?;
    Ok(config_dir.join("hassmpris"))
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    env_logger::builder()
        .format_timestamp(Some(env_logger::TimestampPrecision::Millis))
        .init();

    let cert_path = match args.certificate_file {
        None => xdg_config_dir_for_app()?.join("server.crt"),
        Some(s) => PathBuf::from_str(s.as_str()).expect("Cannot fail"),
    };
    let key_path = match args.key_file {
        None => xdg_config_dir_for_app()?.join("server.key"),
        Some(s) => PathBuf::from_str(s.as_str()).expect("Cannot fail"),
    };
    let ca_cert_path = match args.ca_certificate_file {
        None => xdg_config_dir_for_app()?.join("ca.crt"),
        Some(s) => PathBuf::from_str(s.as_str()).expect("Cannot fail"),
    };
    let ca_key_path = match args.ca_key_file {
        None => xdg_config_dir_for_app()?.join("ca.key"),
        Some(s) => PathBuf::from_str(s.as_str()).expect("Cannot fail"),
    };

    // FIXME gen certs if they do not exist.
    let cert_data = read(cert_path).await?;
    let key_data = read(key_path).await?;
    let ca_cert_data = read(ca_cert_path).await?;
    let ca_key_data = read(ca_key_path).await?;

    let identity = Identity::from_pem(cert_data, key_data);

    let ca_cert = Certificate::from_pem(ca_cert_data);

    let mpris_addr = "0.0.0.0:40051".parse().map_err(|e| Box::new(e))?; // FIXME arg
    info!("MPRISServer will listen on {mpris_addr}");
    let cakes_addr = "0.0.0.0:40052".parse().map_err(|e| Box::new(e))?; // FIXME arg
    info!("CAKESServer will listen on {cakes_addr}");

    let mpris_srv = Server::builder()
        .tls_config(
            ServerTlsConfig::new()
                .identity(identity)
                .client_ca_root(ca_cert)
                .client_auth_optional(false)
                .timeout(Duration::from_secs(TLS_HANDSHAKE_TIMEOUT)),
        )?
        .timeout(Duration::from_secs(REQUEST_TIMEOUT))
        .add_service(grpc::MprisServer::new(grpc::MPRISService::default()))
        .serve(mpris_addr);

    let cakes_srv = Server::builder()
        .timeout(Duration::from_secs(REQUEST_TIMEOUT))
        .add_service(grpc::CakesServer::new(grpc::CAKESService::new()))
        .serve(cakes_addr);

    select! {
        r = cakes_srv => {
            Ok(r?)
        }
        s = mpris_srv => {
            Ok(s?)
        }
    }
}
