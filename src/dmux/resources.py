"""Resource attribution using matched process identities, never inferred jobs."""
import math
import time

from .commands import read_command


class ProcessResources:
    def __init__(self):
        self.previous, self.cache = {}, {}

    def read(self, process, identity, now):
        cached = self.cache.get(identity)
        if cached and now - cached['observed_at'] < 2:
            return cached
        try:
            cpu = process.cpu_times()
            seconds = cpu.user + cpu.system
            memory = process.memory_info().rss
            previous = self.previous.get(identity)
            percent = None
            if previous and now > previous[0] and seconds >= previous[1]:
                percent = 100 * (seconds - previous[1]) / (now - previous[0])
            self.previous[identity] = now, seconds
            result = {'cpu_percent': percent, 'rss_bytes': memory, 'observed_at': now, 'error': None}
        except Exception as exc:
            result = {'cpu_percent': None, 'rss_bytes': None, 'observed_at': now, 'error': type(exc).__name__}
        self.cache[identity] = result
        return result

    def retain(self, identities):
        for mapping in (self.previous, self.cache):
            for key in list(mapping):
                if key not in identities:
                    del mapping[key]


def gpu_processes():
    observed = time.time()
    try:
        text = read_command(['nvidia-smi', '--query-compute-apps=pid,gpu_uuid,used_gpu_memory',
                             '--format=csv,noheader,nounits'])
    except (OSError, ValueError) as exc:
        return {'processes': {}, 'observed_at': observed, 'error': str(exc)}
    records, invalid = {}, 0
    for line in text.splitlines():
        try:
            pid, uuid, memory = [part.strip() for part in line.split(',')]
            amount = float(memory)
            if int(pid) <= 0 or not uuid or not math.isfinite(amount) or amount < 0:
                raise ValueError('invalid process GPU data')
            records.setdefault(str(int(pid)), []).append({'device': uuid, 'used_bytes': int(amount * 1024**2)})
        except (ValueError, TypeError):
            invalid += 1
    return {'processes': records, 'observed_at': observed,
            'error': f'{invalid} GPU process row(s) unavailable' if invalid else None}


def summarize(task, gpu):
    processes = task.get('processes', [])
    readings = [p.get('resources') or {} for p in processes]
    cpu = [r.get('cpu_percent') for r in readings]
    rss = [r.get('rss_bytes') for r in readings]
    devices = gpu.get('process_memory', {})
    gpu_bytes = []
    for process in processes:
        if process['started'] <= devices.get('observed_at', 0):
            gpu_bytes += [r['used_bytes'] for r in devices.get('processes', {}).get(str(process['pid']), [])]
    return {'cpu_percent': sum(cpu) if cpu and all(v is not None for v in cpu) else None,
            'rss_bytes': sum(rss) if rss and all(v is not None for v in rss) else None,
            'gpu_bytes': sum(gpu_bytes) if gpu_bytes else None,
            'gpu_stale': bool(gpu.get('stale')), 'gpu_error': devices.get('error'),
            'errors': sorted({r['error'] for r in readings if r.get('error')})}
