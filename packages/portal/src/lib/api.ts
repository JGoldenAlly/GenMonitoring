// Typed API client for the GenMonitoring REST API.
//
// One function per endpoint, matching the API's actual pydantic schemas
// (packages/api/app/schemas/*.py) field-for-field. Handles JWT storage
// (memory + localStorage), attaching `Authorization: Bearer <token>`, and a
// single-retry-after-refresh 401 flow that redirects to /login if the
// refresh itself fails.

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// ---------------------------------------------------------------------------
// Shared types
// ---------------------------------------------------------------------------

export type Role = 'admin' | 'operator' | 'viewer';

export interface AuthUser {
  id: string;
  email: string;
  role: Role;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface LoginResponse extends TokenPair {
  user: AuthUser;
}

// --- devices ---

export interface DeviceOut {
  id: string;
  device_key: string;
  cpu_serial: string;
  owner_id: string | null;
  claimed: boolean;
  friendly_name?: string | null;
  mqtt_host: string | null;
  mqtt_port: number;
  auto_update_enabled: boolean;
  reporting_interval_seconds: number;
  config_refresh_interval_seconds: number;
  scan_requested: boolean;
  modbus_scan_results: Record<string, unknown> | null;
  logs_requested: boolean;
  sim_notes: string | null;
  last_seen_at: string | null;
  created_at: string;
}

export interface DeviceUpdate {
  auto_update_enabled?: boolean;
  reporting_interval_seconds?: number;
  config_refresh_interval_seconds?: number;
  scan_requested?: boolean;
  logs_requested?: boolean;
  sim_notes?: string | null;
}

export interface ClaimResponse {
  device_key: string;
  claimed: boolean;
  device_bearer_token?: string | null;
}

// --- generators ---

export type ModbusTransport = 'rtu' | 'tcp';

export interface GeneratorOut {
  id: string;
  device_id: string;
  friendly_name: string;
  modbus_transport: ModbusTransport;
  modbus_host: string | null;
  modbus_port: number | null;
  modbus_baud: number | null;
  modbus_parity: string | null;
  modbus_stop_bits: number | null;
  modbus_slave_id: number;
  gpio_out_channel: string | null;
  gpio_in_channel: string | null;
  start_stop_enabled: boolean;
  max_run_session_minutes: number;
  control_inhibited: boolean;
  control_inhibited_reason: string | null;
  control_inhibited_by_user_id?: string | null;
  control_inhibited_at?: string | null;
  current_command_id?: string | null;
  current_desired_state?: 'run' | 'stop' | null;
  current_command_expires_at?: string | null;
  notes?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface GeneratorCreate {
  device_id: string;
  friendly_name: string;
  modbus_transport: ModbusTransport;
  modbus_host?: string | null;
  modbus_port?: number | null;
  modbus_baud?: number | null;
  modbus_parity?: string | null;
  modbus_stop_bits?: number | null;
  modbus_slave_id: number;
  gpio_out_channel?: string | null;
  gpio_in_channel?: string | null;
  start_stop_enabled?: boolean;
  max_run_session_minutes?: number;
  notes?: string | null;
}

export type GeneratorUpdate = Partial<Omit<GeneratorCreate, 'device_id'>>;

export interface ApplyTemplateRequest {
  template_id: string;
  modbus_transport: ModbusTransport;
  modbus_host?: string | null;
  modbus_port?: number | null;
  modbus_baud?: number | null;
  modbus_parity?: string | null;
  modbus_stop_bits?: number | null;
  modbus_slave_id: number;
  friendly_name?: string | null;
  gpio_out_channel?: string | null;
  gpio_in_channel?: string | null;
  start_stop_enabled?: boolean;
}

// --- templates ---

export type RegisterRole = 'running_status' | 'alarm';

export interface TemplateRegisterSpec {
  address: number;
  label: string;
  unit?: string | null;
  register_type: number;
  register_count: number;
  read_interval_seconds: number;
  role?: RegisterRole | null;
}

export interface TemplateOut {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  category: string;
  registers: TemplateRegisterSpec[];
  is_builtin: boolean;
  created_at: string;
}

export interface TemplateCreate {
  name: string;
  description?: string | null;
  category?: string;
  registers: TemplateRegisterSpec[];
}

export type TemplateUpdate = Partial<TemplateCreate>;

// --- readings ---

export interface LatestReadingOut {
  register_address: number;
  register_type: number;
  register_friendly_name: string | null;
  value: number | null;
  unit: string | null;
  time: string;
}

export interface ReadingOut {
  time: string;
  device_key: string;
  register_address: number;
  register_type: number;
  register_friendly_name: string | null;
  value: number | null;
  unit: string | null;
}

// --- commands ---

export type CommandType = 'run' | 'stop' | 'cancel';
export type CommandStatus =
  | 'pending'
  | 'delivered'
  | 'acknowledged'
  | 'expired'
  | 'superseded'
  | 'cancelled';

export interface CommandCreate {
  command_type: CommandType;
  reason?: string | null;
  duration_minutes?: number | null;
}

export interface CommandOut {
  id: string;
  generator_id: string;
  requested_by_user_id: string;
  command_type: CommandType;
  reason: string | null;
  status: CommandStatus;
  expires_at: string | null;
  superseded_by_command_id: string | null;
  acknowledged_at: string | null;
  created_at: string;
}

export interface CommandListOut {
  items: CommandOut[];
  total: number;
  limit: number;
  offset: number;
}

export interface IOStateOut {
  channel: 'IN1' | 'OUT1';
  state: boolean;
  time: string;
  matches_commanded?: boolean | null;
  mismatch_type?: string | null;
}

export interface CurrentCommandOut {
  generator_id: string;
  current_command_id: string | null;
  current_desired_state: 'run' | 'stop' | null;
  current_command_expires_at: string | null;
  control_inhibited: boolean;
  control_inhibited_reason: string | null;
  last_command: CommandOut | null;
  io_states: IOStateOut[];
}

export interface InhibitStatusOut {
  generator_id: string;
  control_inhibited: boolean;
  control_inhibited_reason?: string | null;
  control_inhibited_by_user_id?: string | null;
  control_inhibited_at?: string | null;
}

// --- users ---

export interface UserOut {
  id: string;
  email: string;
  role: Role;
  is_active: boolean;
  created_at: string;
}

export interface UserCreate {
  email: string;
  password: string;
  role: Role;
  is_active?: boolean;
}

export interface UserUpdate {
  email?: string;
  role?: Role;
  is_active?: boolean;
}

// --- api keys ---

export interface ApiKeyOut {
  id: string;
  label: string | null;
  created_at: string;
  last_used_at: string | null;
}

export interface ApiKeyCreated extends ApiKeyOut {
  api_key: string;
}

// ---------------------------------------------------------------------------
// Token storage
// ---------------------------------------------------------------------------

const ACCESS_TOKEN_KEY = 'genmon_access_token';
const REFRESH_TOKEN_KEY = 'genmon_refresh_token';
const USER_KEY = 'genmon_user';

let accessToken: string | null = null;
let refreshToken: string | null = null;
let currentUser: AuthUser | null = null;

function loadFromStorage() {
  if (typeof window === 'undefined') return;
  accessToken = window.localStorage.getItem(ACCESS_TOKEN_KEY);
  refreshToken = window.localStorage.getItem(REFRESH_TOKEN_KEY);
  const rawUser = window.localStorage.getItem(USER_KEY);
  currentUser = rawUser ? (JSON.parse(rawUser) as AuthUser) : null;
}

if (typeof window !== 'undefined') {
  loadFromStorage();
}

function persistSession(tokens: TokenPair, user: AuthUser | null) {
  accessToken = tokens.access_token;
  refreshToken = tokens.refresh_token;
  if (user) currentUser = user;
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
    window.localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
    if (currentUser) {
      window.localStorage.setItem(USER_KEY, JSON.stringify(currentUser));
    }
  }
}

function clearSession() {
  accessToken = null;
  refreshToken = null;
  currentUser = null;
  if (typeof window !== 'undefined') {
    window.localStorage.removeItem(ACCESS_TOKEN_KEY);
    window.localStorage.removeItem(REFRESH_TOKEN_KEY);
    window.localStorage.removeItem(USER_KEY);
  }
}

export function getCurrentUser(): AuthUser | null {
  if (typeof window !== 'undefined' && currentUser === null) {
    loadFromStorage();
  }
  return currentUser;
}

export function isAuthenticated(): boolean {
  if (typeof window !== 'undefined' && accessToken === null) {
    loadFromStorage();
  }
  return !!accessToken;
}

type Listener = () => void;
const authListeners = new Set<Listener>();
export function onAuthChange(listener: Listener): () => void {
  authListeners.add(listener);
  return () => authListeners.delete(listener);
}
function notifyAuthChange() {
  authListeners.forEach((l) => l());
}

function redirectToLogin() {
  clearSession();
  notifyAuthChange();
  if (typeof window !== 'undefined') {
    const next = window.location.pathname + window.location.search;
    window.location.href = `/login?next=${encodeURIComponent(next)}`;
  }
}

// ---------------------------------------------------------------------------
// Core request helper
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  auth?: boolean; // defaults to true
  isRetry?: boolean;
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = new URL(path.replace(/^\/+/, '/'), API_BASE);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

async function extractErrorMessage(res: Response): Promise<{ message: string; body: unknown }> {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // no JSON body
  }
  if (body && typeof body === 'object') {
    const b = body as Record<string, unknown>;
    if (typeof b.detail === 'string') return { message: b.detail, body };
    if (Array.isArray(b.detail)) {
      const msgs = (b.detail as Array<Record<string, unknown>>)
        .map((d) => (typeof d.msg === 'string' ? d.msg : JSON.stringify(d)))
        .join('; ');
      return { message: msgs || res.statusText, body };
    }
  }
  return { message: res.statusText || `Request failed (${res.status})`, body };
}

async function doRefresh(): Promise<boolean> {
  if (!refreshToken) return false;
  try {
    const res = await fetch(buildUrl('/auth/refresh'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) return false;
    const tokens = (await res.json()) as TokenPair;
    persistSession(tokens, currentUser);
    return true;
  } catch {
    return false;
  }
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, query, auth = true, isRetry = false } = options;

  const headers: Record<string, string> = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (auth && accessToken) headers['Authorization'] = `Bearer ${accessToken}`;

  let res: Response;
  try {
    res = await fetch(buildUrl(path, query), {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    throw new ApiError(0, 'Network error contacting the API. Is the API reachable?');
  }

  if (res.status === 401 && auth && !isRetry) {
    const refreshed = await doRefresh();
    if (refreshed) {
      return request<T>(path, { ...options, isRetry: true });
    }
    redirectToLogin();
    throw new ApiError(401, 'Session expired. Redirecting to login.');
  }

  if (!res.ok) {
    const { message, body: errBody } = await extractErrorMessage(res);
    throw new ApiError(res.status, message, errBody);
  }

  if (res.status === 204) {
    return undefined as unknown as T;
  }

  const text = await res.text();
  if (!text) return undefined as unknown as T;
  return JSON.parse(text) as T;
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export async function login(email: string, password: string): Promise<AuthUser> {
  const data = await request<LoginResponse>('/auth/login', {
    method: 'POST',
    body: { email, password },
    auth: false,
  });
  const user: AuthUser = data.user;
  persistSession(
    { access_token: data.access_token, refresh_token: data.refresh_token, token_type: data.token_type },
    user
  );
  notifyAuthChange();
  return user;
}

export function logout() {
  clearSession();
  notifyAuthChange();
  if (typeof window !== 'undefined') {
    window.location.href = '/login';
  }
}

// ---------------------------------------------------------------------------
// Devices
// ---------------------------------------------------------------------------

export const listDevices = () => request<DeviceOut[]>('/devices');

export const updateDevice = (deviceKey: string, update: DeviceUpdate) =>
  request<DeviceOut>(`/devices/${encodeURIComponent(deviceKey)}`, { method: 'PUT', body: update });

export const claimDevice = (deviceKey: string) =>
  request<ClaimResponse>(`/devices/${encodeURIComponent(deviceKey)}/claim`, { method: 'POST', body: {} });

export const unclaimDevice = (deviceKey: string) =>
  request<ClaimResponse>(`/devices/${encodeURIComponent(deviceKey)}/claim`, { method: 'DELETE' });

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

export const listGenerators = (params?: { device_id?: string }) =>
  request<GeneratorOut[]>('/generators', { query: { device_id: params?.device_id } });

export const getGenerator = (id: string) => request<GeneratorOut>(`/generators/${id}`);

export const createGenerator = (payload: GeneratorCreate) =>
  request<GeneratorOut>('/generators', { method: 'POST', body: payload });

export const updateGenerator = (id: string, payload: GeneratorUpdate) =>
  request<GeneratorOut>(`/generators/${id}`, { method: 'PUT', body: payload });

export const deleteGenerator = (id: string) =>
  request<void>(`/generators/${id}`, { method: 'DELETE' });

export const applyTemplate = (deviceKey: string, payload: ApplyTemplateRequest) =>
  request<GeneratorOut>(`/devices/${encodeURIComponent(deviceKey)}/apply-template`, {
    method: 'POST',
    body: payload,
  });

// ---------------------------------------------------------------------------
// Templates
// ---------------------------------------------------------------------------

export const listTemplates = () => request<TemplateOut[]>('/templates');

export const createTemplate = (payload: TemplateCreate) =>
  request<TemplateOut>('/templates', { method: 'POST', body: payload });

export const updateTemplate = (id: string, payload: TemplateUpdate) =>
  request<TemplateOut>(`/templates/${id}`, { method: 'PUT', body: payload });

export const deleteTemplate = (id: string) => request<void>(`/templates/${id}`, { method: 'DELETE' });

// ---------------------------------------------------------------------------
// Readings
// ---------------------------------------------------------------------------

export const getLatestReadings = (generatorId: string) =>
  request<LatestReadingOut[]>(`/generators/${generatorId}/readings/latest`);

export const getReadingsSeries = (
  generatorId: string,
  params: { register_address: number; since?: string; until?: string; limit?: number }
) =>
  request<ReadingOut[]>(`/generators/${generatorId}/readings`, {
    query: {
      register_address: params.register_address,
      since: params.since,
      until: params.until,
      limit: params.limit ?? 200,
    },
  });

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

export const getCurrentCommand = (generatorId: string) =>
  request<CurrentCommandOut>(`/generators/${generatorId}/commands/current`);

export const listCommands = (generatorId: string, params?: { limit?: number; offset?: number }) =>
  request<CommandListOut>(`/generators/${generatorId}/commands`, {
    query: { limit: params?.limit ?? 50, offset: params?.offset ?? 0 },
  });

export const createCommand = (generatorId: string, payload: CommandCreate) =>
  request<CommandOut>(`/generators/${generatorId}/commands`, { method: 'POST', body: payload });

export const cancelCommand = (generatorId: string, commandId: string) =>
  request<CommandOut>(`/generators/${generatorId}/commands/${commandId}/cancel`, { method: 'POST' });

export const setInhibit = (generatorId: string, reason: string) =>
  request<InhibitStatusOut>(`/generators/${generatorId}/inhibit`, { method: 'POST', body: { reason } });

export const clearInhibit = (generatorId: string) =>
  request<InhibitStatusOut>(`/generators/${generatorId}/inhibit`, { method: 'DELETE' });

// ---------------------------------------------------------------------------
// Users
// ---------------------------------------------------------------------------

export const listUsers = () => request<UserOut[]>('/users');

export const createUser = (payload: UserCreate) =>
  request<UserOut>('/users', { method: 'POST', body: payload });

export const updateUser = (id: string, payload: UserUpdate) =>
  request<UserOut>(`/users/${id}`, { method: 'PUT', body: payload });

export const deleteUser = (id: string) => request<void>(`/users/${id}`, { method: 'DELETE' });

export const resetUserPassword = (id: string, newPassword: string) =>
  request<void>(`/users/${id}/password`, { method: 'PUT', body: { new_password: newPassword } });

// ---------------------------------------------------------------------------
// API keys
// ---------------------------------------------------------------------------

export const listApiKeys = () => request<ApiKeyOut[]>('/apikeys');

export const createApiKey = (label?: string) =>
  request<ApiKeyCreated>('/apikeys', { method: 'POST', body: { label: label || null } });

export const revokeApiKey = (id: string) => request<void>(`/apikeys/${id}`, { method: 'DELETE' });
