# Linux desktop agent to allow MPRIS multimedia control from Home Assistant

This package contains the agent that Home Assistant connects to in order
to govern multimedia playback.

## Setup

### Dependencies

Install GTK+ 4 and libnotify on your system.  These should be packages
provided by the system.

### This package

Install this package on your computer, then run the program
`hassmpris-settings` to turn the agent on.

### Firewall rules
Don't forget to open the requisite
firewall ports to allow communication from Home Assistant:

* TCP port 40051
* TCP port 40052

### Pair with Home Assistant

Once the agent is running you can connect to your computer from Home Assistant.

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
