/** Что отдаёт сервер. Имена полей повторяют схему API один в один:
 *  перевод на ходу — лишний слой, в котором опечатка видна не сразу. */

export type Role = 'admin' | 'operator';

/** Именованные действия. Ровно те же, что в матрице прав на сервере. */
export type Permission = 'view' | 'run' | 'settings' | 'prices' | 'send' | 'senders' | 'users';

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
  | 'needs_review'
  | 'priced'
  | 'declined'
  | 'free'
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

export interface ReplyAttachment {
  имя: string;
  байт: number;
  тип: string | null;
  принято: boolean;
}

export interface IncomingCard {
  id: number;
  kind: ReplyKind;
  raw_body: string;
  received_at: string;
  /** Адрес, с которого ответили. Может отличаться от того, кому писали:
   *  на общий ящик смотрит секретарь и пересылает письмо редактору. */
  from_email: string | null;
  subject: string | null;
  /** Прайс приходит вложением чаще, чем текстом: ответ с файлом
   *  не должен выглядеть пустым. */
  attachments: ReplyAttachment[] | null;
  price_white: string | null;
  price_grey: string | null;
  currency: string | null;
  payment_methods: string[] | null;
  confidence: number | null;
  /** Продаёт ли донор размещение по разбору: sells, declines, unclear. */
  placement: string | null;
  /** Ждёт ли разбор человека. Считает сервер: порог живёт в настройках,
   *  и второй его экземпляр на фронте разъехался бы при первой правке. */
  needs_review: boolean;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface ReviewPrice {
  price_white: string | null;
  price_grey: string | null;
  currency: string | null;
  payment_methods: string[];
  /** Донор ответил «не продаём размещения» — поля цены при этом пусты. */
  declines?: boolean;
}

export interface Reviewed {
  id: number;
  reviewed_by: string;
  stored_price: boolean;
  seller_answer: 'sells' | 'declines' | null;
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
  | 'blocked'
  | 'error';
export type ContactSource = 'mx' | 'page' | 'rdap' | 'paid' | 'form' | 'manual';
export type RunStatus = 'queued' | 'estimating' | 'running' | 'done' | 'stopped';

export interface PoolRequest {
  preset: string;
  country: string;
  /** Про что ключи. Пусто — законный исход: широкий пул иногда и нужен.
   *  Несколько тем дают больше доменов на тот же потолок: внутри темы
   *  выдача пересекается сама с собой, между темами почти нет. */
  topics?: string[];
  cap?: number;
}

export interface KeywordPool {
  keywords: string[];
  /** На каких языках собирали. Выводятся из страны, оператор их не задаёт. */
  languages: string[];
  asked: number;
  received: number;
  rejected: number;
  near_duplicates: number;
  /** Отказы модели. Пул, собранный наполовину из-за них, внешне
   *  неотличим от пула, который модель честно не набрала. */
  refusals: string[];
  tokens: number;
  model: string;
}

export interface RunRequest {
  keywords: string[];
  country: string;
  depth_pages: number;
  /** Потолок юнитов на этот прогон. Пусто — весь остаток по капу. */
  cap?: number;
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
  /** Потрачено нами юнитов с начала месяца — на столько уменьшился кап. */
  units_spent_this_month: number;
  /** Остаток по месячному капу. */
  cap_left: number;
  /** Потолок, названный человеком для этого прогона. */
  run_ceiling: number | null;
  /** Ожидаемая стоимость выдачи в долларах. Другой счёт, не юниты. */
  serp_cost_usd: number;
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
  /** Когда прогон последний раз подавал признаки жизни: для идущего это
   *  удар heartbeat, а не запись результата. */
  alive_at: string;
  /** Сколько доменов дала выдача. Появляется раньше любых трат. */
  hosts: number | null;
  /** Сколько доменов прогона посмотрел человек на экране отбора и сколько
   *  раз разошёлся с судьёй. Считается при чтении: решают после прогона. */
  reviewed: number;
  disagreements: number;
  /** Очередь рассмотрения: статус → сколько. Пусто — прогон сделан до очереди. */
  queue: Partial<Record<ReviewDecision, number>>;
}

export interface RunsView {
  runs: RunCard[];
  /** Сколько воркеров слушает очередь. `null` — спросить не удалось,
   *  и это не ноль: неизвестность и пустота требуют разных слов. */
  workers: number | null;
}

export interface RunQueued {
  run_id: number;
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
  last_price: string | null;
  last_price_currency: string | null;
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
  /** Разбивка неполная: спрашивали только верхнюю страну — её хватило
   *  для вердикта, а полный запрос стоит впятеро дороже. */
  geo_partial: boolean;
  metrics: Record<string, unknown> | null;
  expires_at: string | null;
  contact_attempted_at: string | null;
  last_price_at: string | null;
  contacts: ContactCard[];
}

export interface ThresholdsBody {
  min_dr: number;
  min_org_traffic: number;
  min_refdomains: number;
  min_keywords: number;
}

export interface ThresholdsVersion extends ThresholdsBody {
  version: number;
  created_by: string | null;
  created_at: string;
}

export interface ThresholdsView {
  /** Пусто, пока порогов не заводили: тогда действуют умолчания. */
  current: ThresholdsVersion | null;
  defaults: ThresholdsBody;
  history: ThresholdsVersion[];
}

export interface ConsequencesView {
  checked: number;
  suitable_now: number;
  suitable_after: number;
  falls_out: number;
  falls_out_with_price: number;
  comes_back: number;
  unchecked: number;
}

export type UsageProvider = 'ahrefs' | 'serp' | 'llm' | 'email';

export interface ArticleCard {
  provider: UsageProvider;
  operation: string;
  units: number;
  amount_usd: string;
  calls: number;
}

export interface SpendingView {
  since: string;
  articles: ArticleCard[];
  units_by_provider: Record<string, number>;
  amount_by_provider: Record<string, string>;
  total_units: number;
  total_amount: string;
  /** Остаток **у провайдера**: ключ общий с соседней системой, поэтому
   *  её траты тоже уменьшают это число. `null` — спросить не удалось. */
  ahrefs_left: number | null;
  /** Наш добровольный потолок на месяц. */
  ahrefs_cap: number;
  /** Сколько потратили мы, по своей таблице. Именно это сравнивают с капом:
   *  остаток провайдера включает чужой расход и для этого не годится. */
  ahrefs_spent_by_us: number;
  ahrefs_left_error: string | null;
  /** Остаток денег у источника выдачи. Счёт свой, чужого расхода в нём нет. */
  serp_left_usd: string | null;
  serp_spent_by_us: string;
  serp_left_error: string | null;
}

/** Границы коридора отличия. Приходят с сервера: второй экземпляр чисел
 *  на фронте разъехался бы с настройкой при первой её правке, и экран
 *  показывал бы «в коридоре» там, где его уже нет. */
export interface Corridor {
  min: number;
  max: number;
}

export interface LetterTransport {
  name: string;
  /** Уходит ли письмо на самом деле. У нулевого транспорта — нет, и
   *  показать это обязательно: письмо, помеченное отправленным и никуда
   *  не ушедшее, выглядит как работа. */
  real: boolean;
  /** Почему транспорт не собрался, если не собрался. */
  problem: string | null;
}

/** Письмо в очереди — целиком, вместе с текстом. */
export interface Followup {
  /** 1 — первое напоминание, 2 — последнее. */
  step: number;
  subject: string;
  body: string;
  /** Через сколько дней после предыдущего письма уйдёт. */
  in_days: number;
}

export interface QueuedLetter {
  id: number;
  host: string;
  email: string | null;
  campaign: string;
  status: MessageStatus;
  subject: string | null;
  body: string | null;
  /** Доля изменённых слов относительно шаблона, 0–1. */
  uniqueness: number | null;
  /** Что не так с этим числом. Пусто — в коридоре. */
  verdict: string | null;
  /** Добивки этого донора — текстом, каким они уйдут. */
  followups: Followup[];
}

export interface LetterZone {
  name: string;
  /** `rewrite` — переписывает модель под донора, `fixed` — уходит как есть. */
  kind: 'rewrite' | 'fixed';
  title: string;
  text: string;
}

/** Текст первого письма по зонам — для правки перед созданием рассылки. */
export interface LetterDraftView {
  subject: string;
  zones: LetterZone[];
}

/** Поправленный текст: тема и содержимое зон по именам. */
export interface LetterDraft {
  subject: string;
  zones: Record<string, string>;
}

/** Этап рассылки: донорам — вопрос о цене, рекламодателям — оффер под найденную ссылку. */
export type LetterStage = 'donors' | 'advertisers';

export interface LettersView {
  /** Чья это очередь: у этапов свои письма, текст по умолчанию и воронка. */
  stage: LetterStage;
  letters: QueuedLetter[];
  /** Сроки добивок по умолчанию: их предлагает сервер, а не помнит фронт. */
  followup_default: number[];
  /** Текст первого письма по умолчанию — тот, что лежит в коде. */
  letter_default: LetterDraftView;
  /** Настройки, из-за которых отправить нельзя ни одно письмо. */
  blocked_by: string[];
  transport: LetterTransport;
  corridor: Corridor;
  /** Где кончились адресаты: пустая очередь при «всем написали» и при
   *  «ни у кого нет адреса» выглядит одинаково. Ступени у этапов свои. */
  funnel: Record<string, number>;
}

export interface BuildLettersRequest {
  campaign: string;
  /** Кому. Нет — донорам: так сервер понимает и старые запросы. */
  stage?: LetterStage;
  country?: string;
  niche?: string[];
  limit?: number;
  /** Через сколько дней после предыдущего письма уходят добивки. */
  followup_days?: number[];
  /** Поправленный текст первого письма. Нет — текст по умолчанию
   *  у новой рассылки или собственный у найденной. */
  letter?: LetterDraft;
  /** Прогоны, из принятых доноров которых собирается рассылка. У
   *  рекламодателей прогонов нет — сервер откажет, если их прислать. */
  run_ids?: number[];
}

export interface BuildQueued {
  job_id: string;
}

export interface SendResult {
  id: number;
  sender_email: string;
  real: boolean;
}

/** Почему адресату не пишем. Имена те же, что в базе и в журнале. */
export type SuppressionReason = 'unsubscribed' | 'complained' | 'supplier' | 'manual';

export interface StopEntry {
  id: number;
  host: string | null;
  email: string | null;
  reason: SuppressionReason;
  /** Пусто — запрет действует на обоих этапах. */
  stage: 'donors' | 'advertisers' | null;
  created_by: string | null;
  created_at: string;
  /** Докуда запись держит. Пусто — навсегда. */
  expires_at: string | null;
  /**
   * Срок вышел: запись видна, но письма больше не держит. Считает
   * сервер — сравнение дат здесь шло бы по часам браузера.
   */
  expired: boolean;
  /**
   * Решение адресата, а не наше: снимается только с причиной. Считает
   * сервер — свой экземпляр правила на фронте разошёлся бы с ним
   * на первой новой причине.
   */
  donor_decision: boolean;
}

export interface StopListView {
  rows: StopEntry[];
  total: number;
  donor_decisions: number;
  /** Сколько строк уже истекло и никого не держит. */
  expired: number;
}

/** Одна тревога сторожа тишины: что молчит и что это значит. */
export interface AlarmCard {
  code: string;
  title: string;
  detail: string;
}

export interface WatchdogView {
  alarms: AlarmCard[];
}

/** Состояние поиска контактов: сколько ждёт, идёт ли сейчас, чем кончился прошлый. */
export interface ContactsState {
  pending: number;
  running: boolean;
  job_id: string | null;
  last: Record<string, unknown> | null;
  workers: number | null;
  /** Исход последней задачи целиком — в том числе упавшей или ждущей повтора. */
  job?: JobCard | null;
}

/** Исход фоновой задачи словами человека (`/api/jobs/{id}`). */
export type JobState =
  | 'queued'
  | 'running'
  | 'retry_wait'
  | 'done'
  | 'refused'
  | 'failed'
  | 'unknown';

export interface JobCard {
  job_id: string;
  kind: string;
  state: JobState;
  title: string;
  error: string | null;
  report: Record<string, unknown> | null;
  retries_left: number | null;
  next_try_at: string | null;
  ended_at: string | null;
}

export interface ContactsQueued {
  job_id: string;
  pending: number;
}

/** Донор из ручной очереди: у него есть форма и нет адреса. */
export interface FormCard {
  donor_id: number;
  domain_id: number;
  host: string;
  dr: number | null;
  org_traffic: number | null;
  attempted_at: string | null;
}

export interface FormsView {
  rows: FormCard[];
  total: number;
  monthly_left: number;
  monthly_cap: number;
}

/** Кандидат в рекламодатели: балл скоринга и всё, по чему решает человек. */
export interface CandidateCard {
  id: number;
  donor_host: string;
  target_root: string;
  points: number;
  verdict: 'bought' | 'pending' | 'skipped' | 'blocked';
  /** Причины строками: их читают глазами, а не разбирают кодом. */
  reasons: string[];
  links: number;
  pages: number;
  /** Страница и анкор, под которые будет написано письмо. */
  best_page_url: string | null;
  best_anchor: string | null;
  confirmed: boolean | null;
  decided_by: string | null;
  decided_at: string | null;
}

export interface CandidatesView {
  rows: CandidateCard[];
  waiting: number;
  /** Счётчики по всем вердиктам: по отсеянным видно, что список работает. */
  counts: Record<string, number>;
}

// --- отбор: кто принят, кто отклонён, кем и почему ----------------------

export type SelectionTab = 'accepted' | 'review' | 'rejected';
export type JudgeDecider = 'rule' | 'model' | 'arbiter';
export type HumanIntent = 'publisher' | 'sells_own' | 'non_commercial';

export interface MachineView {
  intent: string | null;
  recommendation: 'accept' | 'review' | 'reject' | null;
  decided_by: JudgeDecider | null;
  quote: string | null;
  reason: string | null;
  source_url: string | null;
  home_shop: string[];
  home_reached: boolean | null;
  judged_at: string | null;
}

export interface SellerView {
  answer: 'sells' | 'free' | 'declines' | null;
  answered_at: string | null;
  price: string | null;
  currency: string | null;
}

export interface HumanView {
  intent: HumanIntent | null;
  note: string | null;
  decided_at: string | null;
}

export interface SelectionCard {
  domain_id: number;
  host: string;
  tab: SelectionTab;
  donor_id: number | null;
  status: DonorStatus | null;
  reject_reason: string | null;
  dr: number | null;
  org_traffic: number | null;
  machine: MachineView;
  human: HumanView;
  seller: SellerView;
  disagrees: boolean;
}

export interface SelectionView {
  rows: SelectionCard[];
  total: number;
  tabs: Record<SelectionTab, number>;
  reviewed: number;
  disagreements: number;
  layers: Partial<Record<JudgeDecider, { checked: number; agreed: number }>>;
  /** Сколько доноров ответили, продают ли размещение. */
  answered: number;
  /** Сходимость судьи с ответами доноров — точность отбора в главном. */
  answer_layers: Partial<Record<JudgeDecider, { checked: number; agreed: number }>>;
}

// --- калибровка разбора ответов -----------------------------------------

export interface VersionCalibration {
  version: string;
  reviewed: number;
  as_is: number;
  edited: number;
  wrong: Record<string, number>;
  auto_stored: number;
  waiting: number;
}

export interface CalibrationView {
  versions: VersionCalibration[];
}

// --- Рассмотрение прогона ---

export type ReviewDecision = 'pending' | 'accepted' | 'rejected';
export type ReviewTier = 'likely' | 'open' | 'doubtful';

export interface CandidateCard {
  candidate_id: number;
  domain_id: number;
  host: string;
  status: ReviewDecision;
  /** Ярус по совету судьи: сверху вероятные доноры, внизу сомнительные. */
  tier: ReviewTier;
  /** Решение перенесено из прошлого прогона. */
  carried: boolean;
  decided_by: string | null;
  decided_at: string | null;
  note: string | null;
  dr: number | null;
  org_traffic: number | null;
  geo: string | null;
  geo_top_share: number | null;
  contact_status: ContactStatus | null;
  /** По каким ключам прогона нашёлся домен. */
  found_by: string[];
  machine: MachineView;
  seller: SellerView;
}

export interface ReviewRunHead {
  id: number;
  country: string;
  keywords: number;
  created_at: string;
}

export interface ReviewView {
  run: ReviewRunHead;
  rows: CandidateCard[];
  counts: Record<ReviewDecision, number>;
  /** Скрыто под фильтром сомнительных: судья советует отказ. */
  hidden: number;
}

export interface DecideResult {
  changed: number;
  accepted: number;
  contacts_job_id: string | null;
}

export interface AgreementView {
  advised: number;
  agreed: number;
  precision: number | null;
}

/** Судья против человека: по этим числам решают, доверять ли судье больше. */
export interface AccuracyView {
  decided: number;
  by_advice: Record<string, AgreementView>;
  by_layer: Record<string, Record<string, AgreementView>>;
  by_intent: Record<string, Record<string, AgreementView>>;
  unjudged: number;
  asked_to_review: number;
  auto_accept_ready: boolean;
  auto_accept_precision: number;
  auto_accept_min_decisions: number;
}
