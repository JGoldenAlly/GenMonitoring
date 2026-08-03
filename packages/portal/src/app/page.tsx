'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { isAuthenticated } from '@/lib/api';

export default function RootPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace(isAuthenticated() ? '/devices' : '/login');
  }, [router]);

  return (
    <div className="flex h-64 items-center justify-center text-sm text-neutral-500">Loading GenMonitoring…</div>
  );
}
