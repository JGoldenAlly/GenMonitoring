'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { RequireAuth, useAuth } from '@/lib/auth-context';
import {
  ApiError,
  DeviceOut,
  GeneratorOut,
  listDevices,
  listGenerators,
  unclaimDevice,
  updateDevice,
} from '@/lib/api';
import { formatDateTime, relativeTime } from '@/lib/format';
import Badge from '@/components/Badge';
import ErrorBanner from '@/components/ErrorBanner';

function DeviceDetailInner() {
  const params = useParams<{ deviceKey: string }>();
  const deviceKey = decodeURIComponent(params.deviceKey);
  const router = useRouter();
  const { hasRole } = useAuth();

  const [device, setDevice] = useState<DeviceOut | null>(null);
  const [generators, setGenerators] = useState<GeneratorOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  // form state
  const [reportingInterval, setReportingInterval] = useState(60);
  const [configRefreshInterval, setConfigRefreshInterval] = useState(60);
  const [autoUpdate, setAutoUpdate] = useState(true);
  const [simNotes, setSimNotes] = useState('');

  const canEdit = hasRole('admin', 'operator');

  const refresh = useCallback(async () => {
    try {
      const devices = await listDevices();
      const found = devices.find((d) => d.device_key === deviceKey);
      if (!found) {
        setNotFound(true);
        return;
      }
      setDevice(found);
      setGenerators(await listGenerators({ device_id: found.id }));
      setReportingInterval(found.reporting_interval_seconds);
      setConfigRefreshInterval(found.config_refresh_interval_seconds);
      setAutoUpdate(found.auto_update_enabled);
      setSimNotes(found.sim_notes || '');
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load device.');
    }
  }, [deviceKey]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function onSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const updated = await updateDevice(deviceKey, {
        reporting_interval_seconds: reportingInterval,
        config_refresh_interval_seconds: configRefreshInterval,
        auto_update_enabled: autoUpdate,
        sim_notes: simNotes,
      });
      setDevice(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save device config.');
    } finally {
      setSaving(false);
    }
  }

  async function onUnclaim() {
    if (!confirm(`Unclaim device ${deviceKey}? It will stop being associated with your fleet.`)) return;
    try {
      await unclaimDevice(deviceKey);
      router.push('/devices');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to unclaim device.');
    }
  }

  if (notFound) {
    return <ErrorBanner message={`No device found with key ${deviceKey}.`} />;
  }

  if (!device) {
    return <div className="text-sm text-neutral-500">Loading device…</div>;
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="font-mono text-xl font-semibold">{device.device_key}</h1>
            {device.claimed ? <Badge tone="green">claimed</Badge> : <Badge tone="yellow">unclaimed</Badge>}
          </div>
          <p className="mt-1 text-sm text-neutral-500">
            CPU serial {device.cpu_serial} · last seen {relativeTime(device.last_seen_at)}
          </p>
        </div>
        {canEdit && device.claimed && (
          <button
            onClick={onUnclaim}
            className="rounded border border-red-900 px-3 py-1.5 text-sm text-red-300 hover:bg-red-950/40"
          >
            Unclaim device
          </button>
        )}
      </div>

      <ErrorBanner message={error} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="rounded-lg border border-surface-border bg-surface-card p-4">
          <h2 className="mb-4 text-sm font-semibold text-neutral-300">Configuration</h2>
          <form onSubmit={onSave} className="space-y-4">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">
                Reporting interval (seconds)
              </label>
              <input
                type="number"
                min={5}
                disabled={!canEdit}
                value={reportingInterval}
                onChange={(e) => setReportingInterval(Number(e.target.value))}
                className="w-40"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">
                Config refresh interval (seconds)
              </label>
              <input
                type="number"
                min={5}
                disabled={!canEdit}
                value={configRefreshInterval}
                onChange={(e) => setConfigRefreshInterval(Number(e.target.value))}
                className="w-40"
              />
            </div>
            <div className="flex items-center gap-2">
              <input
                id="auto-update"
                type="checkbox"
                disabled={!canEdit}
                checked={autoUpdate}
                onChange={(e) => setAutoUpdate(e.target.checked)}
                className="h-4 w-4"
              />
              <label htmlFor="auto-update" className="text-sm text-neutral-300">
                Auto-update agent software
              </label>
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">
                Sim / commissioning notes
              </label>
              <textarea
                disabled={!canEdit}
                value={simNotes}
                onChange={(e) => setSimNotes(e.target.value)}
                rows={4}
                className="w-full"
                placeholder="SIM ICCID, APN, site access notes, etc."
              />
            </div>
            {canEdit && (
              <div className="flex items-center gap-3">
                <button
                  type="submit"
                  disabled={saving}
                  className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
                >
                  {saving ? 'Saving…' : 'Save configuration'}
                </button>
                {saved && <span className="text-sm text-emerald-400">Saved.</span>}
              </div>
            )}
          </form>
        </div>

        <div className="rounded-lg border border-surface-border bg-surface-card p-4">
          <h2 className="mb-2 text-sm font-semibold text-neutral-300">Device details</h2>
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-neutral-500">MQTT host</dt>
              <dd>{device.mqtt_host || '-'}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-neutral-500">MQTT port</dt>
              <dd>{device.mqtt_port}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-neutral-500">Created</dt>
              <dd>{formatDateTime(device.created_at)}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-neutral-500">Modbus scan requested</dt>
              <dd>{device.scan_requested ? 'yes' : 'no'}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-neutral-500">Logs requested</dt>
              <dd>{device.logs_requested ? 'yes' : 'no'}</dd>
            </div>
          </dl>
          {device.modbus_scan_results && (
            <div className="mt-3">
              <div className="mb-1 text-xs uppercase tracking-wide text-neutral-500">Last scan results</div>
              <pre className="max-h-40 overflow-auto rounded bg-black/40 p-2 text-xs text-neutral-400">
                {JSON.stringify(device.modbus_scan_results, null, 2)}
              </pre>
            </div>
          )}
        </div>
      </div>

      <div className="mt-6 rounded-lg border border-surface-border">
        <div className="flex items-center justify-between border-b border-surface-border px-4 py-3">
          <h2 className="text-sm font-semibold text-neutral-300">Generators on this device</h2>
          {canEdit && (
            <Link
              href={`/devices/${encodeURIComponent(deviceKey)}/add-generator`}
              className="rounded bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
            >
              + Add generator
            </Link>
          )}
        </div>
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Transport</th>
              <th>Start/stop</th>
              <th>Inhibited</th>
            </tr>
          </thead>
          <tbody>
            {generators.length === 0 && (
              <tr>
                <td colSpan={4} className="text-neutral-500">
                  No generators configured on this device yet.
                </td>
              </tr>
            )}
            {generators.map((g) => (
              <tr key={g.id} className="hover:bg-surface-card/60">
                <td>
                  <Link href={`/generators/${g.id}`} className="text-emerald-400 hover:underline">
                    {g.friendly_name}
                  </Link>
                </td>
                <td className="uppercase text-neutral-400">{g.modbus_transport}</td>
                <td>{g.start_stop_enabled ? <Badge tone="blue">enabled</Badge> : <Badge>disabled</Badge>}</td>
                <td>{g.control_inhibited ? <Badge tone="red">inhibited</Badge> : <Badge tone="green">ok</Badge>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function DeviceDetailPage() {
  return (
    <RequireAuth>
      <DeviceDetailInner />
    </RequireAuth>
  );
}
