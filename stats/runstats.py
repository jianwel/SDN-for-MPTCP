import ConfigParser
import daemonocle
import subprocess
import sys
import threading
import time


# config location
config_path = "test.conf"


# ifstat defaults
ifstat_count = "5"


# ping defaults
fping_count = "5"
fping_ips = ["127.0.0.1", "127.0.0.1"]


# Parse ifstat output as follows:
# [ (<interface 1>, <avg thrpt in 1>, <avg thrpt out 1>),
#   (<interface 2>, <avg thrpt in 2>, <avg thrpt out 2>) ... ]
def parse_ifstat(buffer):
  lines = buffer.split('\n')
  lines = [x.strip() for x in lines if len(x) > 1]
  devices = lines[0].split()

  data = [[[], []] for d in devices]
  for line in lines[2:]:
    line = line.split()
    for i in range(len(line) / 2):           # Per device:
      data[i][0].append(float(line[i*2]))    # KB/s in
      data[i][1].append(float(line[i*2+1]))  # KB/s out

  result = []
  for i in range(len(data)):
    result.append( (
      devices[i],
      str(float(sum(data[i][0]))/len(data[i][0])),     # Average KB/s in
      str(float(sum(data[i][1]))/len(data[i][1])) ) )  # Average KB/s out

  return result


def ifstat_worker():
  while True:
    output = subprocess.check_output(["ifstat", "-i", "eth0", "-n", ifstat_count, "1"])
    data = parse_ifstat(output)

    with open("/var/log/linkstats-tp.log", "w+") as f:
      for d in data:
        f.write(' '.join(d) + '\n')


# Parse fping output as follows:
# [ (<IP 1>, <min rtt 1>, <avg rtt 1>, <max rtt 1>),
#   (<IP 2>, <min rtt 2>, <avg rtt 2>, <max rtt 2>) ... ]
def parse_fping(buffer):
  lines = buffer.split('\n')
  lines = [x.strip() for x in lines if len(x) > 1 and x.find('[') < 0]

  result = []
  for line in lines:
    parts = line.split()
    ip = parts[0]
    rttparts = parts[7].split('/')
    result.append( (ip, rttparts[0], rttparts[1], rttparts[2]) )

  return result
    

def fping_worker():
  while True:
    args = ["fping", "-c", fping_count]
    args[1:1] = fping_ips
    output = subprocess.check_output(args, stderr=subprocess.STDOUT)
    data = parse_fping(output)

    with open("/var/log/linkstats-rtt.log", "w+") as f:
      for d in data:
        f.write(' '.join(d) + '\n')


def start_workers():
  t_ifstat = threading.Thread(target=ifstat_worker)
  t_fping = threading.Thread(target=fping_worker)
  
  # Threads exit immediately when daemon is stopped
  t_ifstat.daemon = True
  t_fping.daemon = True
  
  t_ifstat.start()
  t_fping.start()


def main():
  config = ConfigParser.RawConfigParser()
  config.read(config_path)

  ifstat_count = config.get('ifstat', 'count')
  
  fping_count = config.get('fping', 'count')
  fping_ips = config.get('fping', 'ip').split(';')
  
  start_workers()

  # Wait indefinitely in main thread
  while threading.active_count() > 0:
    time.sleep(0.1)


if __name__ == '__main__':
  #main()
  daemon = daemonocle.Daemon(
    worker=main,
    pidfile='/var/run/runstats.pid',
  )
  daemon.do_action(sys.argv[1])

