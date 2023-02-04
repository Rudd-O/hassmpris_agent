# Linux desktop agent to allow MPRIS multimedia control from Home Assistant

This package contains the agent that Home Assistant connects to in order
to govern multimedia playback.

## What is this program for?

This program lets your computer's media players (compatible with the MPRIS
standard) be remotely controlled by any compatible client (most common being
[Home Assistant](https://home-assistant.io/)), when you are logged into your
computer's desktop session.  Headless operation should also be supported
(although it is not regularly exercised) so long as the program runs in a
D-Bus session shared by other MPRIS-compatible media players as well.

A small graphical utility that lets you turn this program on or off is
shipped with this package as well.

## Supported media players

In general, all media players compliant with the MPRIS specification should
work to varying degrees of compatibility.  That said, here is a list of
media players known to work, and their supported features:

* VLC
  * Play / pause / stop.
  * Playback rate change.
  * Next / previous track.
  * Seek.
* Google Chrome / Chromium
  * Play / pause / stop.
  * Next / previous track.
  * Seek.
* Amarok
  * Play / pause / stop.
  * Next / previous track.
  * Seek.
* Spotifyd
  * Nothing works — it hangs when its MPRIS interface is queried.
* MPD
  * Requires the [mpd-mpris](https://github.com/natsukagami/mpd-mpris) service

If you test another media player, report your test results (along with any
errors you find, and logs from this program) to the project's
[issue tracker](https://github.com/Rudd-O/hassmpris_agent/issues).

## Setup

The general process is:

1. Install GTK+ 4 and libnotify on your system.
2. Then install / upgrade this package.  If upgrading,
   log out then log back in after installation.
3. Finally, run the settings program to turn the agent on,
   or (if upgrading) to verify that the agent is running.

See the options below for instructions on various systems.

### Install from PyPI

Ensure GTK+ 4 and libnotify are installed on your system by using your
system package manager.

Then use `pip install --user -U hassmpris_agent`.  Find the
`hassmpris-settings` and `hassmpris-agent` programs in your
`~/.local/bin` directory.

*Never install anything using `pip` to your system Python
library directory.  It can cause problems for you down the road.*

### Install as an RPM package

Pre-built packages for various Fedora releases are available at
https://repo.rudd-o.com/ .  These take care of installing all the required
dependencies properly.

Find the `hassmpris-settings` and `hassmpris-agent` programs
in your system `$PATH` (generally `/usr/bin`).

### Run the agent

#### Within your graphical desktop session (as usual)

Run the program `hassmpris-settings` to start the settings program.  If this
program is not readily available, run `python3 -m hassmpris_agent.settings`
instead.

A window will pop up, with a slider to turn the agent on.  Slide the slider
to the *on* position to start the agent.  From then on, the agent will auto
start every time you log in.

#### Manually (e.g. in a headless scenario)

Run the program `hassmpris-agent`.  This program must be run **after**
the session has a successfully-executed D-Bus session daemon, otherwise
the D-Bus client within the program will attempt to auto-launch D-Bus
and this will not work without an X11 or Wayland graphical session.

The program should work without issue in a headless session, providing
remote access to any media players sharing the same D-Bus session with
the agent.  If it does not, please file an issue in this project's
Github repository.

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
