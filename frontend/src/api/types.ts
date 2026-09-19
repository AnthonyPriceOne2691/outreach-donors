/** Что отдаёт сервер. Имена полей повторяют схему API один в один:
 *  перевод на ходу — лишний слой, в котором опечатка видна не сразу. */

export type Role = 'admin' | 'operator';

/** Именованные действия. Ровно те же, что в матрице прав на сервере. */
export type Permission = 'view' | 'run' | 'settings' | 'send' | 'senders' | 'users';

export interface Me {
  id: number;
  email: string;
  role: Role;
  /** Что человек может на самом деле — роль вместе с точечными исключениями.
   *  Приходит готовым списком, чтобы фронт не повторял у себя матрицу прав
   *  и не разъезжался с ней: кнопка есть, а запрос отказывает. */
  permissions: Permission[];
  must_change_password: boolean;
  last_login_at: string | null;
}

export interface SignedIn {
  token: string;
  token_type: string;
  expires_in_hours: number;
  user: Me;
}

export interface UserCard extends Me {
  /** Чем права отличаются от роли. Отобрать обратно можно только это. */
  overrides: Partial<Record<Permission, boolean>>;
  is_active: boolean;
}

export interface OneTimePassword {
  user: UserCard;
  password: string;
  note: string;
}

export interface AccessPatch {
  role?: Role;
  is_active?: boolean;
  permissions?: Partial<Record<Permission, boolean>>;
}

/** Состояние ящика рассылки со стороны отправки. */
export type SenderStatus = 'free' | 'working' | 'paused';

export interface SenderCard {
  id: number;
  domain: string;
  email: string;
  enabled: boolean;
  status: SenderStatus;
  sent_today: number;
  daily_cap: number;
  /** День разгона; 0 — разгон закончен. */
  warmup_day: number;
  /** Сколько писем можно сегодня: на разгоне это меньше дневного капа. */
  warmup_allowance: number;
  warmup_finished: boolean;
  paused_at: string | null;
  pause_reason: string | null;
}

export interface SendersView {
  senders: SenderCard[];
  /** Сколько доменов сейчас может отправлять. Ноль означает, что рассылка
   *  остановлена целиком — об этом предупреждают до нажатия. */
  enabled_domains: number;
}

/** Состояние диалога. Считается сервером по событиям, не хранится. */
export type ThreadState =
  | 'queued'
  | 'waiting'
  | 'replied'
  | 'priced'
  | 'bounced'
  | 'unsubscribed'
  | 'stopped';

export interface ThreadCard {
  id: number;
  host: string;
  contact_email: string | null;
  campaign: string;
  state: ThreadState;
  messages_sent: number;
  last_event_at: string | null;
  last_reply_at: string | null;
  price_white: string | null;
  price_grey: string | null;
  currency: string | null;
}

export type MessageStatus = 'queued' | 'sending' | 'sent' | 'delivered' | 'bounced' | 'stopped';

export interface LetterCard {
  id: number;
  step: number;
  status: MessageStatus;
  subject: string | null;
  body: string | null;
  sent_at: string | null;
  uniqueness_pct: number | null;
}

export type ReplyKind = 'human' | 'auto_reply' | 'bounce' | 'unsubscribe';

export interface IncomingCard {
  id: number;
  kind: ReplyKind;
  raw_body: string;
  received_at: string;
  price_white: string | null;
  price_grey: string | null;
  currency: string | null;
  payment_methods: string[] | null;
  confidence: number | null;
}

export interface ThreadView {
  card: ThreadCard;
  letters: LetterCard[];
  incoming: IncomingCard[];
}

export type DonorStatus = 'suitable' | 'unsuitable' | 'unchecked';
export type ContactStatus =
  | 'found'
  | 'not_found'
  | 'form_only'
  | 'no_quota'
  | 'rate_limited'
  | 'error';
export type ContactSource = 'mx' | 'page' | 'rdap' | 'paid' | 'form' | 'manual';
export type RunStatus = 'estimating' | 'running' | 'done' | 'stopped';

export interface RunRequest {
  keywords: string[];
  country: string;
  depth_pages: number;
}

export interface Forecast {
  keywords: number;
  depth_pages: number;
  expected_results: number;
  expected_domains: number;
  units_screen: number;
  units_metrics: number;
  units_by_country: number;
  units_total: number;
  units_left: number;
  units_cap: number;
  budget: number;
  /** Помещается ли смета в бюджет. По этому полю блокируется кнопка. */
  affordable: boolean;
  shortfall: number;
}

export interface RunCard {
  id: number;
  status: RunStatus;
  country: string;
  keywords: number;
  estimated_units: number | null;
  actual_units: number | null;
  /** На сколько смета разошлась с фактом, в долях. */
  estimate_error: number | null;
  stats: Record<string, unknown> | null;
  started_at: string;
}

export interface RunQueued {
  job_id: string;
  note: string;
}

export interface DonorRowCard {
  id: number;
  host: string;
  status: DonorStatus;
  reject_reason: string | null;
  dr: number | null;
  org_traffic: number | null;
  geo: string | null;
  geo_top_share: number | null;
  contacts: number;
  contact_status: ContactStatus | null;
  last_price_usd: string | null;
  metrics_refreshed_at: string | null;
  /** Данные ещё в сроке годности: за них уже заплачено. */
  fresh: boolean;
}

export interface DonorsPage {
  rows: DonorRowCard[];
  total: number;
  counts: Record<string, number>;
}

export interface ContactCard {
  id: number;
  email: string;
  source: ContactSource;
  last_contacted_at: string | null;
  last_replied_at: string | null;
}

export interface DonorFullCard extends Omit<DonorRowCard, 'contacts'> {
  geo_breakdown: { country: string; share: number }[] | null;
  metrics: Record<string, unknown> | null;
  expires_at: string | null;
  contact_attempted_at: string | null;
  last_price_at: string | null;
  contacts: ContactCard[];
}
