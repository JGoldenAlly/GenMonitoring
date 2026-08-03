'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useAuth } from '@/lib/auth-context';
import { classNames } from '@/lib/format';

const links = [
  { href: '/devices', label: 'Devices' },
  { href: '/generators', label: 'Generators' },
  { href: '/templates', label: 'Templates' },
  { href: '/users', label: 'Users', adminOnly: true },
  { href: '/api-keys', label: 'API Keys' },
];

export default function NavHeader() {
  const { user, logout, hasRole } = useAuth();
  const pathname = usePathname();

  if (!user) return null;

  return (
    <header className="border-b border-surface-border bg-surface-raised">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
        <div className="flex items-center gap-6">
          <Link href="/devices" className="flex items-center gap-2 font-semibold tracking-tight text-neutral-100">
            <span className="inline-block h-2.5 w-2.5 rounded-full bg-emerald-500" />
            GenMonitoring
          </Link>
          <nav className="flex items-center gap-1">
            {links
              .filter((l) => !l.adminOnly || hasRole('admin'))
              .map((l) => {
                const active = pathname === l.href || pathname?.startsWith(l.href + '/');
                return (
                  <Link
                    key={l.href}
                    href={l.href}
                    className={classNames(
                      'rounded px-3 py-1.5 text-sm transition-colors',
                      active
                        ? 'bg-surface-card text-neutral-50'
                        : 'text-neutral-400 hover:bg-surface-card hover:text-neutral-100'
                    )}
                  >
                    {l.label}
                  </Link>
                );
              })}
          </nav>
        </div>
        <div className="flex items-center gap-3 text-sm text-neutral-400">
          <span>
            {user.email} <span className="text-neutral-600">·</span>{' '}
            <span className="uppercase tracking-wide text-neutral-500">{user.role}</span>
          </span>
          <button
            onClick={logout}
            className="rounded border border-surface-border px-3 py-1.5 text-neutral-300 hover:border-red-800 hover:text-red-300"
          >
            Log out
          </button>
        </div>
      </div>
    </header>
  );
}
