# SDN-for-MPTCP
UCLA Winter 2016
---
---
Each participating node in a network runs the stat collection daemon runstats.py, which periodically updates two log files (/var/log/runstats-tp.log and /var/log/runstats-rtt.log). The log files will be read by a central SDN controller to determine the quality of links among neighbors.

To start the daemon, run:

$ sudo python runstats.py start

To stop reporting, run:

$ sudo python runstats.py stop

---
Prerequisites for running the script:
- daemonocle: https://pypi.python.org/pypi/daemonocle/0.1
