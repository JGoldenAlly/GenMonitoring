// Small formatting helpers shared across pages.

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 'unknown';
  const now = Date.now();
  const diffMs = now - then;
  const future = diffMs < 0;
  const abs = Math.abs(diffMs);

  const seconds = Math.round(abs / 1000);
  const minutes = Math.round(seconds / 60);
  const hours = Math.round(minutes / 60);
  const days = Math.round(hours / 24);

  let out: string;
  if (seconds < 45) out = `${seconds}s`;
  else if (minutes < 60) out = `${minutes}m`;
  else if (hours < 24) out = `${hours}h`;
  else out = `${days}d`;

  return future ? `in ${out}` : `${out} ago`;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '-';
  return d.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function formatCountdown(iso: string | null | undefined): string {
  if (!iso) return '-';
  const target = new Date(iso).getTime();
  if (Number.isNaN(target)) return '-';
  const diff = target - Date.now();
  if (diff <= 0) return 'expired';
  const totalSeconds = Math.floor(diff / 1000);
  const mins = Math.floor(totalSeconds / 60);
  const secs = totalSeconds % 60;
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-';
  if (Number.isInteger(value)) return String(value);
  return value.toFixed(digits);
}

export function classNames(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ');
}
