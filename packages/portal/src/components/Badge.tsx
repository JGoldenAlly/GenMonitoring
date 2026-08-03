import { classNames } from '@/lib/format';

type Tone = 'neutral' | 'green' | 'red' | 'yellow' | 'blue';

const toneClasses: Record<Tone, string> = {
  neutral: 'bg-neutral-800 text-neutral-300 border-neutral-700',
  green: 'bg-emerald-950 text-emerald-300 border-emerald-800',
  red: 'bg-red-950 text-red-300 border-red-800',
  yellow: 'bg-amber-950 text-amber-300 border-amber-800',
  blue: 'bg-sky-950 text-sky-300 border-sky-800',
};

export default function Badge({
  children,
  tone = 'neutral',
  className,
}: {
  children: React.ReactNode;
  tone?: Tone;
  className?: string;
}) {
  return (
    <span
      className={classNames(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium',
        toneClasses[tone],
        className
      )}
    >
      {children}
    </span>
  );
}
