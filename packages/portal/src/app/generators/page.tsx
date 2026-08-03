'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { RequireAuth } from '@/lib/auth-context';
import {
  ApiError,
  CurrentCommandOut,
  DeviceOut,
  GeneratorOut,
  getCurrentCommand,
  listDevices,
  listGenerators,
} from '@/lib/api';
import Badge from '@/components/Badge';
import ErrorBanner from '@/components/ErrorBanner';

const POLL_MS = 15000;

function runningState(current: CurrentCommandOut | undefined): { label: string; tone: 'green' | 'neutral' | 'yellow' } {
  if (!current) return { label: '…', tone: 'neutral' };
  const out1 = current.io_states.find((s) => s.channel === 'OUT1');
  if (out1) {
    return out1.state ? { label: 'running', tone: 'green' } : { label: 'stopped', tone: 'neutral' };
  }
  if (current.current_desired_state === 'run') return { label: 'pending run', tone: 'yellow' };
  return { label: 'stopped', tone: 'neutral' };
}

function hasMismatch(current: CurrentCommandOut | undefined): boolean {
  if (!current) return false;
  return current.io_states.some((s) => s.matches_commanded === false);
}

function GeneratorsPageInner() {
  const [generators, setGenerators] = useState<GeneratorOut[] | null>(null);
  const [devicesById, setDevicesById] = useState<Record<string, DeviceOut>>({});
  const [currentByGen, setCurrentByGen] = useState<Record<string, CurrentCommandOut>>({});
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshList = useCallback(async () => {
    try {
      const [gens, devices] = await Promise.all([listGenerators(), listDevices()]);
      setGenerators(gens);
      const map: Record<string, DeviceOut> = {};
      devices.forEach((d) => (map[d.id] = d));
      setDevicesById(map);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load generators.');
    }
  }, []);

  const refreshCurrent = useCallback(async (gens: GeneratorOut[]) => {
    const results = await Promise.allSettled(gens.map((g) => getCurrentCommand(g.id)));
    setCurrentByGen((prev) => {
      const next = { ...prev };
      results.forEach((r, i) => {
        if (r.status === 'fulfilled') next[gens[i].id] = r.value;
      });
      return next;
    });
  }, []);

  useEffect(() => {
    refreshList();
  }, [refreshList]);

  useEffect(() => {
    if (!generators) return;
    refreshCurrent(generators);
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(() => refreshCurrent(generators), POLL_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generators]);

  return (
    <div>
      <h1 className="mb-6 text-xl font-semibold">Generators</h1>
      <ErrorBanner message={error} />
      <div className="overflow-x-auto rounded-lg border border-surface-border">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Device</th>
              <th>State</th>
              <th>Start/stop</th>
              <th>Inhibited</th>
            </tr>
          </thead>
          <tbody>
            {generators === null && (
              <tr>
                <td colSpan={5} className="text-neutral-500">
                  Loading generators…
                </td>
              </tr>
            )}
            {generators !== null && generators.length === 0 && (
              <tr>
                <td colSpan={5} className="text-neutral-500">
                  No generators configured yet. Add one from a device&apos;s detail page.
                </td>
              </tr>
            )}
            {generators?.map((g) => {
              const device = devicesById[g.device_id];
              const current = currentByGen[g.id];
              const state = runningState(current);
              const mismatch = hasMismatch(current);
              return (
                <tr key={g.id} className="hover:bg-surface-card/60">
                  <td>
                    <Link href={`/generators/${g.id}`} className="text-emerald-400 hover:underline">
                      {g.friendly_name}
                    </Link>
                  </td>
                  <td className="font-mono text-neutral-400">
                    {device ? (
                      <Link href={`/devices/${device.device_key}`} className="hover:underline">
                        {device.device_key}
                      </Link>
                    ) : (
                      '-'
                    )}
                  </td>
                  <td>
                    <span className="flex items-center gap-2">
                      <Badge tone={state.tone}>{state.label}</Badge>
                      {mismatch && (
                        <span title="Externally-detected mismatch: physical I/O disagreed with the last command">
                          <Badge tone="red">⚠ mismatch</Badge>
                        </span>
                      )}
                    </span>
                  </td>
                  <td>{g.start_stop_enabled ? <Badge tone="blue">enabled</Badge> : <Badge>disabled</Badge>}</td>
                  <td>{g.control_inhibited ? <Badge tone="red">inhibited</Badge> : <Badge tone="green">ok</Badge>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function GeneratorsPage() {
  return (
    <RequireAuth>
      <GeneratorsPageInner />
    </RequireAuth>
  );
}
