'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { RequireAuth, useAuth } from '@/lib/auth-context';
import {
  ApiError,
  CommandOut,
  CurrentCommandOut,
  DeviceOut,
  GeneratorOut,
  LatestReadingOut,
  ModbusTransport,
  ReadingOut,
  UserOut,
  cancelCommand,
  clearInhibit,
  createCommand,
  deleteGenerator,
  getCurrentCommand,
  getGenerator,
  getLatestReadings,
  getReadingsSeries,
  listCommands,
  listDevices,
  listUsers,
  setInhibit,
  updateGenerator,
} from '@/lib/api';
import { formatCountdown, formatDateTime, formatNumber, relativeTime } from '@/lib/format';
import Badge from '@/components/Badge';
import ErrorBanner from '@/components/ErrorBanner';
import Modal from '@/components/Modal';
import StatTile from '@/components/StatTile';
import Sparkline from '@/components/Sparkline';

const IDLE_POLL_MS = 30000;
const ACTIVE_POLL_MS = 5000;
const TELEMETRY_POLL_MS = 10000;

const DURATION_OPTIONS = [15, 30, 60, 90, 120, 240, 480];

function commandIcon(type: string) {
  if (type === 'run') return '▶';
  if (type === 'stop') return '■';
  return '✕';
}

function statusTone(status: string): 'green' | 'red' | 'yellow' | 'neutral' | 'blue' {
  if (status === 'acknowledged') return 'green';
  if (status === 'pending' || status === 'delivered') return 'yellow';
  if (status === 'expired' || status === 'cancelled') return 'neutral';
  if (status === 'superseded') return 'blue';
  return 'neutral';
}

function GeneratorDetailInner() {
  const params = useParams<{ id: string }>();
  const generatorId = params.id;
  const router = useRouter();
  const { user, hasRole } = useAuth();

  const [generator, setGenerator] = useState<GeneratorOut | null>(null);
  const [device, setDevice] = useState<DeviceOut | null>(null);
  const [latest, setLatest] = useState<LatestReadingOut[] | null>(null);
  const [current, setCurrent] = useState<CurrentCommandOut | null>(null);
  const [history, setHistory] = useState<CommandOut[]>([]);
  const [usersById, setUsersById] = useState<Record<string, UserOut>>({});
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  const [selectedRegister, setSelectedRegister] = useState<number | null>(null);
  const [series, setSeries] = useState<ReadingOut[]>([]);
  const [seriesLoading, setSeriesLoading] = useState(false);

  const [showStartModal, setShowStartModal] = useState(false);
  const [showExtendModal, setShowExtendModal] = useState(false);
  const [showInhibitModal, setShowInhibitModal] = useState(false);
  const [showSettingsModal, setShowSettingsModal] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());

  const canOperate = hasRole('admin', 'operator');
  const canClearInhibit = hasRole('admin');

  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const telemetryTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const tickTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadGenerator = useCallback(async () => {
    try {
      const found = await getGenerator(generatorId);
      setGenerator(found);
      const devices = await listDevices();
      setDevice(devices.find((d) => d.id === found.device_id) || null);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setNotFound(true);
        return;
      }
      setError(err instanceof ApiError ? err.message : 'Failed to load generator.');
    }
  }, [generatorId]);

  const loadLatest = useCallback(async () => {
    try {
      const data = await getLatestReadings(generatorId);
      setLatest(data);
      if (selectedRegister === null && data.length > 0) {
        setSelectedRegister(data[0].register_address);
      }
    } catch (err) {
      // Non-fatal: readings may not exist yet for a brand-new generator.
      setLatest((prev) => prev ?? []);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generatorId]);

  const loadCurrent = useCallback(async () => {
    try {
      const data = await getCurrentCommand(generatorId);
      setCurrent(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load command state.');
    }
  }, [generatorId]);

  const loadHistory = useCallback(async () => {
    try {
      const data = await listCommands(generatorId, { limit: 50 });
      setHistory(data.items);
    } catch (err) {
      // non-fatal
    }
  }, [generatorId]);

  useEffect(() => {
    loadGenerator();
    loadCurrent();
    loadHistory();
  }, [loadGenerator, loadCurrent, loadHistory]);

  useEffect(() => {
    if (hasRole('admin')) {
      listUsers()
        .then((users) => {
          const map: Record<string, UserOut> = {};
          users.forEach((u) => (map[u.id] = u));
          setUsersById(map);
        })
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.role]);

  // Telemetry polling
  useEffect(() => {
    loadLatest();
    telemetryTimer.current = setInterval(loadLatest, TELEMETRY_POLL_MS);
    return () => {
      if (telemetryTimer.current) clearInterval(telemetryTimer.current);
    };
  }, [loadLatest]);

  // Command state polling — faster while a session looks active.
  const isActive = current?.current_desired_state === 'run' && !!current?.current_command_expires_at;
  useEffect(() => {
    if (pollTimer.current) clearInterval(pollTimer.current);
    pollTimer.current = setInterval(loadCurrent, isActive ? ACTIVE_POLL_MS : IDLE_POLL_MS);
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, [isActive, loadCurrent]);

  // 1s local countdown ticker.
  useEffect(() => {
    tickTimer.current = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      if (tickTimer.current) clearInterval(tickTimer.current);
    };
  }, []);

  useEffect(() => {
    if (selectedRegister === null) return;
    setSeriesLoading(true);
    getReadingsSeries(generatorId, { register_address: selectedRegister, limit: 200 })
      .then(setSeries)
      .catch(() => setSeries([]))
      .finally(() => setSeriesLoading(false));
  }, [generatorId, selectedRegister]);

  async function refreshAfterAction() {
    await Promise.all([loadCurrent(), loadHistory(), loadGenerator()]);
  }

  async function onStop() {
    if (!confirm('Send a stop command to this generator now?')) return;
    setActionError(null);
    try {
      await createCommand(generatorId, { command_type: 'stop' });
      await refreshAfterAction();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Failed to stop generator.');
    }
  }

  async function onCancelPending() {
    if (!current?.current_command_id) return;
    if (!confirm('Cancel the pending command?')) return;
    setActionError(null);
    try {
      await cancelCommand(generatorId, current.current_command_id);
      await refreshAfterAction();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Failed to cancel command.');
    }
  }

  async function onDeleteGenerator() {
    if (!generator) return;
    if (!confirm(`Delete generator "${generator.friendly_name}"? This removes its configuration and history.`)) return;
    setActionError(null);
    try {
      await deleteGenerator(generator.id);
      router.push('/generators');
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Failed to delete generator.');
    }
  }

  async function onClearInhibit() {
    if (!confirm('Clear the inhibit lock and allow remote control again?')) return;
    setActionError(null);
    try {
      await clearInhibit(generatorId);
      await refreshAfterAction();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Failed to clear inhibit.');
    }
  }

  const startDisabledReason = useMemo(() => {
    if (!user) return 'Not signed in.';
    if (!canOperate) return 'Your role (viewer) cannot issue start/stop commands.';
    if (generator?.control_inhibited) return `Control is inhibited: ${generator.control_inhibited_reason || 'no reason given'}.`;
    if (!generator?.start_stop_enabled) return 'Start/stop control is not enabled for this generator.';
    return null;
  }, [user, canOperate, generator]);

  if (notFound) return <ErrorBanner message="Generator not found." />;
  if (!generator) return <div className="text-sm text-neutral-500">Loading generator…</div>;

  const out1 = current?.io_states.find((s) => s.channel === 'OUT1');
  const in1 = current?.io_states.find((s) => s.channel === 'IN1');
  const mismatch = current?.io_states.find((s) => s.matches_commanded === false);

  return (
    <div>
      <div className="mb-6 flex items-start justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-xl font-semibold">{generator.friendly_name}</h1>
            {device && (
              <span className="font-mono text-sm text-neutral-500">{device.device_key}</span>
            )}
            <Badge tone={generator.start_stop_enabled ? 'blue' : 'neutral'}>
              {generator.start_stop_enabled ? 'start/stop enabled' : 'monitor only'}
            </Badge>
          </div>
          <p className="mt-1 text-sm text-neutral-500 uppercase">
            {generator.modbus_transport}
            {generator.modbus_transport === 'tcp'
              ? ` · ${generator.modbus_host}:${generator.modbus_port}`
              : ` · ${generator.modbus_baud} baud, parity ${generator.modbus_parity}, ${generator.modbus_stop_bits} stop bit(s)`}
            {` · slave ${generator.modbus_slave_id}`}
          </p>
        </div>
        {canOperate && (
          <div className="flex shrink-0 gap-2">
            <button
              onClick={() => setShowSettingsModal(true)}
              className="rounded border border-surface-border px-3 py-1.5 text-sm text-neutral-300 hover:bg-surface-raised"
            >
              Edit settings
            </button>
            <button
              onClick={onDeleteGenerator}
              className="rounded border border-red-900 px-3 py-1.5 text-sm text-red-300 hover:bg-red-950/40"
            >
              Delete
            </button>
          </div>
        )}
      </div>

      {generator.control_inhibited && (
        <div className="mb-6 rounded-lg border border-red-800 bg-red-950/50 px-4 py-3 text-red-200">
          <div className="font-semibold">⛔ Control inhibited</div>
          <div className="text-sm">{generator.control_inhibited_reason || 'No reason recorded.'}</div>
          {canClearInhibit && (
            <button
              onClick={onClearInhibit}
              className="mt-2 rounded border border-red-700 px-3 py-1 text-sm hover:bg-red-900/50"
            >
              Clear inhibit (admin)
            </button>
          )}
          {!canClearInhibit && (
            <div className="mt-1 text-xs text-red-400">Only an admin can clear an inhibit lock.</div>
          )}
        </div>
      )}

      <ErrorBanner message={error} />
      <ErrorBanner message={actionError} />

      {/* Control panel */}
      <div className="mb-6 rounded-lg border border-surface-border bg-surface-card p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-neutral-300">Start / stop control</h2>
          <div className="flex items-center gap-4 text-sm text-neutral-400">
            <span>
              OUT1: {out1 ? <Badge tone={out1.state ? 'green' : 'neutral'}>{out1.state ? 'closed (run)' : 'open (stop)'}</Badge> : '-'}
            </span>
            <span>
              IN1: {in1 ? <Badge tone={in1.state ? 'green' : 'neutral'}>{in1.state ? 'active' : 'inactive'}</Badge> : '-'}
            </span>
          </div>
        </div>

        {mismatch && (
          <div className="mb-4 rounded border border-amber-800 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
            ⚠ Mismatch detected on {mismatch.channel}: physical state did not match the commanded state
            {mismatch.mismatch_type ? ` (${mismatch.mismatch_type.replace('_', ' ')})` : ''} at{' '}
            {formatDateTime(mismatch.time)}.
          </div>
        )}

        <div className="flex flex-wrap items-center gap-3">
          {isActive ? (
            <>
              <div
                key={now}
                className="rounded border border-emerald-800 bg-emerald-950/40 px-3 py-1.5 text-sm text-emerald-300"
              >
                Running · expires in {formatCountdown(current?.current_command_expires_at)}
              </div>
              <button
                disabled={!canOperate || generator.control_inhibited}
                onClick={() => setShowExtendModal(true)}
                className="rounded bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
              >
                Extend
              </button>
              <button
                disabled={!canOperate}
                onClick={onStop}
                className="rounded bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-500 disabled:opacity-50"
              >
                Stop
              </button>
            </>
          ) : (
            <>
              <button
                disabled={!!startDisabledReason}
                title={startDisabledReason || undefined}
                onClick={() => setShowStartModal(true)}
                className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                Start
              </button>
              {startDisabledReason && <span className="text-xs text-neutral-500">{startDisabledReason}</span>}
              {current?.current_command_id && current.last_command?.status === 'pending' && (
                <button
                  onClick={onCancelPending}
                  className="rounded border border-surface-border px-3 py-1.5 text-sm text-neutral-300 hover:bg-surface-raised"
                >
                  Cancel pending command
                </button>
              )}
            </>
          )}

          <div className="ml-auto flex items-center gap-2">
            {canOperate && !generator.control_inhibited && (
              <button
                onClick={() => setShowInhibitModal(true)}
                className="rounded border border-amber-800 px-3 py-1.5 text-sm text-amber-300 hover:bg-amber-950/40"
              >
                Set inhibit
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Telemetry */}
      <div className="mb-6 rounded-lg border border-surface-border bg-surface-card p-5">
        <h2 className="mb-4 text-sm font-semibold text-neutral-300">Live telemetry</h2>
        {latest === null && <div className="text-sm text-neutral-500">Loading readings…</div>}
        {latest !== null && latest.length === 0 && (
          <div className="text-sm text-neutral-500">No readings reported yet.</div>
        )}
        {latest !== null && latest.length > 0 && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {latest.map((r) => (
              <StatTile
                key={r.register_address}
                label={r.register_friendly_name || `Reg ${r.register_address}`}
                value={r.value === null ? null : Number(formatNumber(r.value))}
                unit={r.unit}
                timestamp={r.time}
              />
            ))}
          </div>
        )}
      </div>

      {/* Time series */}
      <div className="mb-6 rounded-lg border border-surface-border bg-surface-card p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-neutral-300">Trend</h2>
          {latest && latest.length > 0 && (
            <select
              value={selectedRegister ?? ''}
              onChange={(e) => setSelectedRegister(Number(e.target.value))}
              className="w-64"
            >
              {latest.map((r) => (
                <option key={r.register_address} value={r.register_address}>
                  {r.register_friendly_name || `Reg ${r.register_address}`}
                </option>
              ))}
            </select>
          )}
        </div>
        {seriesLoading ? (
          <div className="text-sm text-neutral-500">Loading trend…</div>
        ) : (
          <Sparkline
            points={series.map((r) => ({ time: r.time, value: r.value }))}
            unit={series[0]?.unit}
          />
        )}
      </div>

      {/* Audit history */}
      <div className="rounded-lg border border-surface-border">
        <div className="border-b border-surface-border px-4 py-3">
          <h2 className="text-sm font-semibold text-neutral-300">Command history</h2>
        </div>
        <table>
          <thead>
            <tr>
              <th>Command</th>
              <th>Status</th>
              <th>Requested by</th>
              <th>Reason</th>
              <th>Expires</th>
              <th>Acknowledged</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {history.length === 0 && (
              <tr>
                <td colSpan={7} className="text-neutral-500">
                  No commands issued yet.
                </td>
              </tr>
            )}
            {history.map((c) => (
              <tr key={c.id}>
                <td>
                  <span className="mr-1">{commandIcon(c.command_type)}</span>
                  <span className="uppercase">{c.command_type}</span>
                </td>
                <td>
                  <Badge tone={statusTone(c.status)}>{c.status}</Badge>
                </td>
                <td className="text-neutral-400">
                  {usersById[c.requested_by_user_id]?.email || c.requested_by_user_id.slice(0, 8)}
                </td>
                <td className="max-w-xs truncate text-neutral-400" title={c.reason || ''}>
                  {c.reason || '-'}
                </td>
                <td>{formatDateTime(c.expires_at)}</td>
                <td>{formatDateTime(c.acknowledged_at)}</td>
                <td>{formatDateTime(c.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <StartModal
        open={showStartModal}
        onClose={() => setShowStartModal(false)}
        generator={generator}
        onSubmitted={refreshAfterAction}
      />
      <ExtendModal
        open={showExtendModal}
        onClose={() => setShowExtendModal(false)}
        generator={generator}
        onSubmitted={refreshAfterAction}
      />
      <InhibitModal
        open={showInhibitModal}
        onClose={() => setShowInhibitModal(false)}
        generatorId={generatorId}
        onSubmitted={refreshAfterAction}
      />
      <SettingsModal
        open={showSettingsModal}
        onClose={() => setShowSettingsModal(false)}
        generator={generator}
        onSubmitted={refreshAfterAction}
      />
    </div>
  );
}

function StartModal({
  open,
  onClose,
  generator,
  onSubmitted,
}: {
  open: boolean;
  onClose: () => void;
  generator: GeneratorOut;
  onSubmitted: () => void;
}) {
  const [reason, setReason] = useState('');
  const [duration, setDuration] = useState(Math.min(30, generator.max_run_session_minutes));
  const [ack, setAck] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const options = DURATION_OPTIONS.filter((d) => d <= generator.max_run_session_minutes);
  if (options.length === 0) options.push(generator.max_run_session_minutes);

  const reasonValid = reason.trim().length >= 10;
  const canSubmit = reasonValid && ack && !submitting;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      await createCommand(generator.id, { command_type: 'run', reason: reason.trim(), duration_minutes: duration });
      setReason('');
      setAck(false);
      onClose();
      onSubmitted();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to start generator.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Start generator — remote control">
      <ErrorBanner message={error} />
      <form onSubmit={onSubmit} className="space-y-4">
        <p className="rounded border border-amber-800 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
          This will remotely close OUT1 on the field device, energizing the generator controller&apos;s remote
          start circuit for <strong>{generator.friendly_name}</strong>.
        </p>
        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">
            Reason (required, min 10 characters)
          </label>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            className="w-full"
            placeholder="e.g. Scheduled load test per maintenance ticket #1234"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">
            Run duration (minutes, capped at {generator.max_run_session_minutes})
          </label>
          <select value={duration} onChange={(e) => setDuration(Number(e.target.value))} className="w-40">
            {options.map((d) => (
              <option key={d} value={d}>
                {d} min
              </option>
            ))}
          </select>
        </div>
        <label className="flex items-start gap-2 text-sm text-neutral-300">
          <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-1 h-4 w-4" />
          I understand this will remotely start physical equipment.
        </label>
        <div className="flex justify-end gap-3">
          <button type="button" onClick={onClose} className="rounded border border-surface-border px-4 py-2 text-sm text-neutral-300">
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {submitting ? 'Starting…' : 'Confirm start'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ExtendModal({
  open,
  onClose,
  generator,
  onSubmitted,
}: {
  open: boolean;
  onClose: () => void;
  generator: GeneratorOut;
  onSubmitted: () => void;
}) {
  const [reason, setReason] = useState('Extending active run session');
  const [duration, setDuration] = useState(Math.min(30, generator.max_run_session_minutes));
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const options = DURATION_OPTIONS.filter((d) => d <= generator.max_run_session_minutes);
  if (options.length === 0) options.push(generator.max_run_session_minutes);

  const reasonValid = reason.trim().length >= 10;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!reasonValid) return;
    setSubmitting(true);
    setError(null);
    try {
      await createCommand(generator.id, { command_type: 'run', reason: reason.trim(), duration_minutes: duration });
      onClose();
      onSubmitted();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to extend run session.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Extend run session">
      <ErrorBanner message={error} />
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Reason</label>
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} className="w-full" />
        </div>
        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">
            New duration from now (minutes)
          </label>
          <select value={duration} onChange={(e) => setDuration(Number(e.target.value))} className="w-40">
            {options.map((d) => (
              <option key={d} value={d}>
                {d} min
              </option>
            ))}
          </select>
        </div>
        <div className="flex justify-end gap-3">
          <button type="button" onClick={onClose} className="rounded border border-surface-border px-4 py-2 text-sm text-neutral-300">
            Cancel
          </button>
          <button
            type="submit"
            disabled={!reasonValid || submitting}
            className="rounded bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
          >
            {submitting ? 'Extending…' : 'Extend'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function InhibitModal({
  open,
  onClose,
  generatorId,
  onSubmitted,
}: {
  open: boolean;
  onClose: () => void;
  generatorId: string;
  onSubmitted: () => void;
}) {
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const reasonValid = reason.trim().length > 0;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!reasonValid) return;
    setSubmitting(true);
    setError(null);
    try {
      await setInhibit(generatorId, reason.trim());
      setReason('');
      onClose();
      onSubmitted();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to set inhibit.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Set inhibit lock">
      <ErrorBanner message={error} />
      <form onSubmit={onSubmit} className="space-y-4">
        <p className="text-sm text-neutral-400">
          Setting an inhibit lock immediately blocks all start commands for this generator until an admin clears
          it.
        </p>
        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Reason (required)</label>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            className="w-full"
            placeholder="e.g. Technician on-site performing manual maintenance"
          />
        </div>
        <div className="flex justify-end gap-3">
          <button type="button" onClick={onClose} className="rounded border border-surface-border px-4 py-2 text-sm text-neutral-300">
            Cancel
          </button>
          <button
            type="submit"
            disabled={!reasonValid || submitting}
            className="rounded bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-500 disabled:opacity-50"
          >
            {submitting ? 'Setting…' : 'Set inhibit'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function SettingsModal({
  open,
  onClose,
  generator,
  onSubmitted,
}: {
  open: boolean;
  onClose: () => void;
  generator: GeneratorOut;
  onSubmitted: () => void;
}) {
  const [friendlyName, setFriendlyName] = useState(generator.friendly_name);
  const [transport, setTransport] = useState<ModbusTransport>(generator.modbus_transport);
  const [host, setHost] = useState(generator.modbus_host || '');
  const [port, setPort] = useState(generator.modbus_port || 502);
  const [baud, setBaud] = useState(generator.modbus_baud || 9600);
  const [parity, setParity] = useState(generator.modbus_parity || 'N');
  const [stopBits, setStopBits] = useState(generator.modbus_stop_bits || 1);
  const [slaveId, setSlaveId] = useState(generator.modbus_slave_id);
  const [gpioOut, setGpioOut] = useState(generator.gpio_out_channel || '');
  const [gpioIn, setGpioIn] = useState(generator.gpio_in_channel || '');
  const [startStopEnabled, setStartStopEnabled] = useState(generator.start_stop_enabled);
  const [maxRunMinutes, setMaxRunMinutes] = useState(generator.max_run_session_minutes);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Re-sync form fields whenever a different/updated generator is passed in.
  useEffect(() => {
    setFriendlyName(generator.friendly_name);
    setTransport(generator.modbus_transport);
    setHost(generator.modbus_host || '');
    setPort(generator.modbus_port || 502);
    setBaud(generator.modbus_baud || 9600);
    setParity(generator.modbus_parity || 'N');
    setStopBits(generator.modbus_stop_bits || 1);
    setSlaveId(generator.modbus_slave_id);
    setGpioOut(generator.gpio_out_channel || '');
    setGpioIn(generator.gpio_in_channel || '');
    setStartStopEnabled(generator.start_stop_enabled);
    setMaxRunMinutes(generator.max_run_session_minutes);
  }, [generator]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await updateGenerator(generator.id, {
        friendly_name: friendlyName,
        modbus_transport: transport,
        modbus_host: transport === 'tcp' ? host : null,
        modbus_port: transport === 'tcp' ? port : null,
        modbus_baud: transport === 'rtu' ? baud : null,
        modbus_parity: transport === 'rtu' ? parity : null,
        modbus_stop_bits: transport === 'rtu' ? stopBits : null,
        modbus_slave_id: slaveId,
        gpio_out_channel: gpioOut || null,
        gpio_in_channel: gpioIn || null,
        start_stop_enabled: startStopEnabled,
        max_run_session_minutes: maxRunMinutes,
      });
      onClose();
      onSubmitted();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to save generator settings.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Generator settings" wide>
      <ErrorBanner message={error} />
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Friendly name</label>
          <input value={friendlyName} onChange={(e) => setFriendlyName(e.target.value)} required className="w-full" />
        </div>

        <div>
          <div className="mb-1 text-xs uppercase tracking-wide text-neutral-500">Modbus transport</div>
          <div className="flex gap-4 text-sm">
            <label className="flex items-center gap-2">
              <input type="radio" checked={transport === 'tcp'} onChange={() => setTransport('tcp')} />
              TCP
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" checked={transport === 'rtu'} onChange={() => setTransport('rtu')} />
              RTU (serial)
            </label>
          </div>
        </div>

        {transport === 'tcp' ? (
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Host / IP</label>
              <input value={host} onChange={(e) => setHost(e.target.value)} required className="w-full" />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Port</label>
              <input type="number" value={port} onChange={(e) => setPort(Number(e.target.value))} required className="w-full" />
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Baud rate</label>
              <input type="number" value={baud} onChange={(e) => setBaud(Number(e.target.value))} required className="w-full" />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Parity</label>
              <select value={parity} onChange={(e) => setParity(e.target.value)} className="w-full">
                <option value="N">None</option>
                <option value="E">Even</option>
                <option value="O">Odd</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Stop bits</label>
              <select value={stopBits} onChange={(e) => setStopBits(Number(e.target.value))} className="w-full">
                <option value={1}>1</option>
                <option value={2}>2</option>
              </select>
            </div>
          </div>
        )}

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">Modbus slave ID</label>
            <input type="number" min={1} value={slaveId} onChange={(e) => setSlaveId(Number(e.target.value))} required className="w-full" />
          </div>
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">
              Max run session (minutes)
            </label>
            <input
              type="number"
              min={1}
              value={maxRunMinutes}
              onChange={(e) => setMaxRunMinutes(Number(e.target.value))}
              required
              className="w-full"
            />
          </div>
        </div>

        <div className="border-t border-surface-border pt-4">
          <div className="mb-2 flex items-center gap-2">
            <input
              id="settings-start-stop"
              type="checkbox"
              checked={startStopEnabled}
              onChange={(e) => setStartStopEnabled(e.target.checked)}
              className="h-4 w-4"
            />
            <label htmlFor="settings-start-stop" className="text-sm text-neutral-300">
              Enable remote start/stop control (only one generator per device may have this enabled)
            </label>
          </div>
          {startStopEnabled && (
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">GPIO out channel</label>
                <input value={gpioOut} onChange={(e) => setGpioOut(e.target.value)} placeholder="OUT1" className="w-full" />
              </div>
              <div>
                <label className="mb-1 block text-xs uppercase tracking-wide text-neutral-500">GPIO in channel</label>
                <input value={gpioIn} onChange={(e) => setGpioIn(e.target.value)} placeholder="IN1" className="w-full" />
              </div>
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3">
          <button type="button" onClick={onClose} className="rounded border border-surface-border px-4 py-2 text-sm text-neutral-300">
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {submitting ? 'Saving…' : 'Save settings'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export default function GeneratorDetailPage() {
  return (
    <RequireAuth>
      <GeneratorDetailInner />
    </RequireAuth>
  );
}
