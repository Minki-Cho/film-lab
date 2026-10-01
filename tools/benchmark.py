"""Record 20-run P95 performance on an actual ARW; never invent measurements."""
import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from film_lab.engine import render, export_item, inspect
from film_lab.models import atomic_json


def host_details():
    data = {'platform': platform.platform(), 'processor': platform.processor(), 'logical_cpus': os.cpu_count()}
    if os.name == 'nt':
        import winreg
        import ctypes as c
        from ctypes import wintypes as w
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
                data['processor'] = winreg.QueryValueEx(key, 'ProcessorNameString')[0].strip()
        except OSError:
            pass
        class Memory(c.Structure):
            _fields_ = [('length', w.DWORD), ('load', w.DWORD)] + [(name, c.c_ulonglong) for name in
                ('total_physical', 'available_physical', 'total_page', 'available_page', 'total_virtual', 'available_virtual', 'available_extended')]
        memory = Memory()
        memory.length = c.sizeof(memory)
        if c.windll.kernel32.GlobalMemoryStatusEx(c.byref(memory)):
            data['ram_bytes'] = memory.total_physical
        class Counters(c.Structure):
            _fields_ = [('cb', w.DWORD), ('page_faults', w.DWORD)] + [(name, c.c_size_t) for name in
                ('peak_working_set', 'working_set', 'quota_peak_paged', 'quota_paged', 'quota_peak_nonpaged', 'quota_nonpaged', 'pagefile', 'peak_pagefile')]
        counters = Counters()
        counters.cb = c.sizeof(counters)
        c.windll.kernel32.GetCurrentProcess.restype = w.HANDLE
        c.windll.psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, c.c_void_p, w.DWORD]
        c.windll.psapi.GetProcessMemoryInfo.restype = w.BOOL
        if c.windll.psapi.GetProcessMemoryInfo(c.windll.kernel32.GetCurrentProcess(), c.byref(counters), counters.cb):
            data['engine_process_peak_working_set_bytes'] = counters.peak_working_set
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('--runs', type=int, default=20)
    parser.add_argument('--output', default='test-output/benchmark')
    parser.add_argument('--export', action='store_true', help='Also measure full-resolution JPEG; writes each result')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ['FILM_LAB_CACHE'] = str(output / 'cache')
    source = inspect(args.source)
    samples = []
    for index in range(args.runs):
        style = 'documentary' if index % 2 else 'cinema'
        start = time.perf_counter()
        render(args.source, {'style_id': style}, cache_dir=str(output / 'cache'), request_id=str(index))
        item = {'run': index + 1, 'preview_seconds': time.perf_counter() - start}
        if args.export:
            result = export_item(args.source, {'style_id': style}, {'output_dir': str(output), 'format': 'jpeg'})
            item['export_seconds'] = result['elapsed_seconds']
            item['export_status'] = result['status']
        samples.append(item)
        print(json.dumps(item), flush=True)
    def p95(key):
        ordered = sorted(s[key] for s in samples)
        return ordered[min(len(ordered) - 1, __import__('math').ceil(.95 * len(ordered)) - 1)]
    report = {**host_details(), 'source': source, 'runs': samples, 'preview_p95_seconds': p95('preview_seconds'),
              'notes': 'First run cold; second style reuses decoded baseline; later identical preview settings reuse PNG. Exports process full resolution every run. Peak engine RSS excludes desktop GUI process. This is one photo repeated20 times, not a20-photo acceptance corpus.'}
    if args.export:
        report['export_p95_seconds'] = p95('export_seconds')
    atomic_json(output / 'benchmark.json', report)


if __name__ == '__main__':
    main()
