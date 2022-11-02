# Linux desktop agent to allow MPRIS multimedia control from Home Assistant

This package contains the agent that Home Assistant connects to in order
to govern multimedia playback.

## Setup

The general process is:

1. Install GTK+ 4 and libnotify on your system.
2. Then install this package.
3. Finally, run the program `hassmpris-settings` to turn the agent on.

See the options below for instructions on various systems.

### From PyPI

Ensure GTK+ 4 and libnotify are installed on your system by using your
system package manager.

Then use `pip install --user -U hassmpris_agent`.  Find the
`hassmpris-settings` program in your `~/.local/bin` directory.

### As an RPM package

Pre-built packages for various Fedora releases are available at
https://repo.rudd-o.com/ .  These take care of installing all the required
dependencies properly.

Find the `hassmpris-settings` program on your system path.

### Firewall rules

On the system running the agent, don't forget to open the requisite firewall
ports, to allow Home Assistant to connect to your agent:

* TCP port 40051
* TCP port 40052

### Pair with Home Assistant

Once the agent is running you can connect to your computer from Home Assistant.
Add the MPRIS integration in your Home Assistant instance, optionally
specifying the address of your machine where this agent is running.  Then
follow the instructions onscreen in both your agent machine and your Home
Assistant interface to complete the pairing process.

## Troubleshooting and help

The [client utility available here](https://github.com/Rudd-O/hassmpris_client)
will help you debug issues by allowing you to connect to the agent from your
machine or another machine.

If the agent is giving you trouble or not working as it's meant to, you may want
to look at your system logs.  E.g. if running the agent under your desktop
session, look at the log files for the session using `journalctl` or under the
file `~/.xsession-errors`.  You should make a copy of any traceback of interest.

### Found a bug or a traceback?

Please report it in the [project's issue tracker](https://github.com/Rudd-O/hassmpris_agent/issues).

## Technical information

The MPRIS desktop agent is composed of two different servers:

* An authentication server (listening on TCP port 40052).
* An MPRIS gRPC server (listening on TCP port 40051).

### The authentication server

The authentication server doles out credentials for clients that
want to connect to the MPRIS gRPC server.  It follows [the CAKES
scheme documented in that project](https://github.com/Rudd-O/cakes)
and implemented in the
[reference HASS MPRIS client](https://github.com/Rudd-O/hassmpris-client).

### The MPRIS gRPC server

The MPRIS gRPC server provides an event-based interface to properly-
authenticated clients, relaying status information as it happens
to them via a bidirectional gRPC channel, and accepting commands
for the media players running locally via that gRPC channel.

This server implements a gRPC interface formalized in package
[hassmpris](https://github.com/Rudd-O/hassmpris)
([direct link to protobuf](https://github.com/Rudd-O/hassmpris/blob/master/src/hassmpris/proto/mpris.proto)).
The protobuf interface documents what commands and properties are
supported at any point in time, and the README.md file of that project
contains useful information as well.

### Interface between gRPC and desktop media players in the agent

Bound to the gRPC server is a D-Bus interface listener that monitors
media players and relays that information back to the gRPC server
for broadcast to remote clients, as well as accepting command requests
from the gRPC client and effecting those commands onto the media
players of the system where this program runs.

In addition to providing a command and event interface for MPRIS
media players, the D-Bus interface listener also provides façades
for certain media players that are not necessarily fully compliant
with the MPRIS specification.
