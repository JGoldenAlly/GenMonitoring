'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { RequireAuth, useAuth } from '@/lib/auth-context';
import { ApiError, claimDevice, DeviceOut, listDevices } from '@/lib/api';
import { relativeTime } from '@/lib/format';
import Badge from '@/components/Badge';
import ErrorBanner from '@/components/ErrorBanner';

function DevicesPageInner() {
  const { hasRole } = useAuth();
  const [devices, setDevices] = useState<DeviceOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [claimKey, setClaimKey] = useState('');
  const [claiming, setClaiming] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await listDevices();
      setDevices(data.sort((a, b) => a.device_key.localeCompare(b.device_key)));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load devices.');
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 20000);
    return () => clearInterval(id);
  }, [refresh]);

  async function onClaim(e: React.FormEvent) {
    e.preventDefault();
    if (!claimKey.trim()) return;
    setClaiming(true);
    setError(null);
    try {
      await claimDevice(claimKey.trim());
      setClaimKey('');
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to claim device.');
    } finally {
      setClaiming(false);
    }
  }

  const unclaimed = (devices || []).filter((d) => !d.claimed);
  const canClaim = hasRole('admin', 'operator');

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Devices</h1>
      </div>

      <ErrorBanner message={error} />

      {canClaim && (
        <div className="mb-6 rounded-lg border border-surface-border bg-surface-card p-4">
          <h2 className="mb-2 text-sm font-semibold text-neutral-300">Claim a device</h2>
          <form onSubmit={onClaim} className="flex flex-wrap items-end gap-3">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Device key</label>
              <input
                value={claimKey}
                onChange={(e) => setClaimKey(e.target.value)}
                placeholder="GM-XXXX-XXXX"
                list="pending-devices"
                className="w-56"
              />
              <datalist id="pending-devices">
                {unclaimed.map((d) => (
                  <option key={d.id} value={d.device_key} />
                ))}
              </datalist>
            </div>
            <button
              type="submit"
              disabled={claiming || !claimKey.trim()}
              className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              {claiming ? 'Claiming…' : 'Claim device'}
            </button>
            {unclaimed.length > 0 && (
              <span className="text-xs text-neutral-500">
                {unclaimed.length} pending / unclaimed device{unclaimed.length === 1 ? '' : 's'} awaiting claim
              </span>
            )}
          </form>
        </div>
      )}

      <div className="overflow-x-auto rounded-lg border border-surface-border">
        <table>
          <thead>
            <tr>
              <th>Device key</th>
              <th>Friendly name</th>
              <th>Status</th>
              <th>Last seen</th>
              <th>Auto-update</th>
              <th>Reporting interval</th>
            </tr>
          </thead>
          <tbody>
            {devices === null && (
              <tr>
                <td colSpan={6} className="text-neutral-500">
                  Loading devices…
                </td>
              </tr>
            )}
            {devices !== null && devices.length === 0 && (
              <tr>
                <td colSpan={6} className="text-neutral-500">
                  No devices registered yet.
                </td>
              </tr>
            )}
            {devices?.map((d) => (
              <tr key={d.id} className="hover:bg-surface-card/60">
                <td>
                  <Link href={`/devices/${d.device_key}`} className="font-mono text-emerald-400 hover:underline">
                    {d.device_key}
                  </Link>
                </td>
                <td>{d.friendly_name || <span className="text-neutral-600">-</span>}</td>
                <td>
                  {d.claimed ? <Badge tone="green">claimed</Badge> : <Badge tone="yellow">unclaimed</Badge>}
                </td>
                <td>{relativeTime(d.last_seen_at)}</td>
                <td>{d.auto_update_enabled ? <Badge tone="blue">on</Badge> : <Badge>off</Badge>}</td>
                <td>{d.reporting_interval_seconds}s</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function DevicesPage() {
  return (
    <RequireAuth>
      <DevicesPageInner />
    </RequireAuth>
  );
}
