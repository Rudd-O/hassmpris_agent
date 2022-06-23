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

## Troubleshooting

The [client utility available here](https://github.com/Rudd-O/hassmpris_client)
will help you debug issues by allowing you to connect to the agent from your
machine or another machine.
