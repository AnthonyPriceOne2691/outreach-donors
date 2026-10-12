/**
 * Что отдаёт сервер разделу «Продажи»: гипотезы, лиды, загрузка базы. Стоп-лист, очистка
 * и воронка — своими файлами (`salesStoplist.ts`, `salesCleanTypes.ts`, `salesFunnelTypes.ts`):
 * этот стоит у предела длины.
 *
 * Своим файлом, а не в `types.ts`: тот стоит в тающем baseline длины (988 строк
 * при пределе 500, запас — на точечные правки до сплита), и гейт длины новый
 * раздел туда не пускает — это первый шов будущего сплита типов по разделам.
 * Правило то же: имена полей повторяют схему API один в один. Коды состояний
 * и полей сверяет с сервером `tests/test_api_sales_screen.py`.
 */

/** Где лид на пути к первому письму (`LeadStatus` сервера): загружен и ещё не
 *  очищен, прошёл очистку, отсеян. Почему отсеян — код `rejection_reason`. */
export type LeadState = 'new' | 'ready' | 'rejected';

/** Откуда лид: файл или таблица, коллега, названный в ответе, — или сбор по ключам (6.1). */
export type LeadSource = 'import' | 'referral' | 'collected';

/** Поле лида, в которое ложится колонка файла (`columns.LeadField` сервера);
 *  сверку с сервером держит `tests/test_api_sales_screen.py`. */
export type LeadField =
  | 'email'
  | 'name'
  | 'first_name'
  | 'last_name'
  | 'position'
  | 'company'
  | 'website'
  | 'country'
  | 'timezone'
  | 'language';

export interface HypothesisCard {
  id: number;
  name: string;
  description: string | null;
  created_at: string;
  /** Состояние → сколько лидов. Все состояния названы, хоть и нулём. */
  leads: Record<LeadState, number>;
  total: number;
  /** Лидов «готов», которых очистка проверит заново: выдуманный вердикт при живом
   *  проверяльщике. Сервер шлёт всегда; в типе необязательно — записанные ответы тестов
   *  раздела его не несут. */
  recheck?: number;
}

export interface HypothesesView {
  rows: HypothesisCard[];
  total: number;
  /** Модуль продаж включён. Выключен — строка под шапкой раздела на любой вкладке. */
  module_enabled: boolean;
}

/** Новая гипотеза из окна «Новая гипотеза» (`POST /sales/hypotheses`). Пустоту и длину
 *  имени, занятое имя судит сервер тем же правилом, что команда консоли. */
export interface HypothesisBody {
  name: string;
  description: string | null;
}

export interface LeadCard {
  id: number;
  email: string;
  name: string | null;
  position: string | null;
  company: string | null;
  /** Домен компании — словом, а не номером: его читают в строке. */
  host: string;
  /** Код страны ISO-2 нижним регистром, пояс — имя из базы поясов. */
  country: string | null;
  timezone: string | null;
  language: string | null;
  hypothesis_id: number;
  hypothesis: string;
  source: LeadSource;
  status: LeadState;
  /** Код причины отказа — по нему фильтр; слова — `cleaning_note`. */
  rejection_reason: string | null;
  cleaning_note: string | null;
  /** Вердикт проверяльщика с именем источника: `fixture:valid`, `hunter:valid`. */
  verification_status: string | null;
  /** Почему адрес не проверен по-настоящему — словами сервера: выдуманный вердикт при живом
   *  проверяльщике. Необязательно в типе — как `recheck` у гипотезы. */
  unproven?: string | null;
  created_at: string;
}

export interface LeadsView {
  rows: LeadCard[];
  total: number;
  /** Номер страницы и её размер — размер называет сервер. */
  page: number;
  limit: number;
  /** Сводка по всем лидам, не по фильтру. Ключи `reasons` — полный перечень
   *  кодов причин, который знает очистка: из него строится фильтр. */
  states: Record<LeadState, number>;
  reasons: Record<string, number>;
}

/** Лид из предпросмотра: адрес и домен компании уже нормализованы. */
export interface ImportedLead {
  line: number;
  email: string;
  domain: string;
  name: string | null;
  position: string | null;
  company: string | null;
  country: string | null;
  timezone: string | null;
  language: string | null;
}

/** Строка отчёта загрузки: номер строки файла, причина словами, ячейка как есть.
 *  `loaded` — лид загружен, пусто только это поле; иначе строка отклонена. */
export interface ImportProblem {
  line: number;
  reason: string;
  cell: string;
  loaded: boolean;
}

/** Умолчания загрузки, как их понял сервер: язык кодом, страна кодом и пояс. */
export interface IntakeDefaults {
  language: string | null;
  country: string | null;
  timezone: string | null;
}

/** Предпросмотр или итог загрузки базы. `loaded` пуст — в базу ничего не записано. */
export interface IntakeView {
  source: string;
  header: boolean;
  columns: string[];
  /** Первые строки данных — по ним человек сопоставляет колонки руками. */
  sample: string[][];
  /** Поле → колонка с нуля. */
  mapping: Partial<Record<LeadField, number>>;
  /** Колонка почты не найдена: лиды не считались, нужно сопоставить руками. */
  needs_mapping: boolean;
  rows: number;
  accepted: number;
  rejected: number;
  /** Первые лиды — что получится; сколько всего — `accepted`. */
  leads: ImportedLead[];
  /** Отчёт целиком: каждая отклонённая строка и каждое замечание. */
  problems: ImportProblem[];
  defaults: IntakeDefaults;
  /** Сколько лидов получили язык и страну или пояс по умолчанию: колонка была пуста. */
  defaulted_language: number;
  defaulted_place: number;
  /** Без чего письма не уйдут — словами сервера, с числом лидов. Пусто — уйдут всем. */
  warnings: string[];
  loaded: number | null;
}

/** Вид записи базы знаний (`KbKind` сервера): по виду агент берёт факты под ход. */
export type KbKind =
  | 'brief'
  | 'service'
  | 'case'
  | 'objection'
  | 'price_policy'
  | 'forbidden'
  | 'cta';

/** Запись базы знаний: что в ней, видит ли её агент, кто и когда правил. */
export interface KbEntryCard {
  id: number;
  kind: KbKind;
  /** Код языка нижним регистром, как у лида: `ru`, `en`, `pt-br`. */
  language: string;
  title: string;
  text: string;
  tags: string[];
  active: boolean;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
}

/** Границы полей записи — их называет сервер, экран проверяет ими поле. */
export interface KbLimits {
  title: number;
  text: number;
  tag: number;
  tags: number;
}

/** Пустой раздел базы на языке писем: что агент сделает без него — словами сервера. */
export interface KbGap {
  kind: KbKind;
  language: string;
  /** Языки, на которых записи вида есть: их агент переведёт. Пусто — нет ни на одном. */
  elsewhere: string[];
  words: string;
}

export interface KbView {
  rows: KbEntryCard[];
  total: number;
  /** Сколько включено — их видит агент. */
  active: number;
  /** Версия базы, которую сейчас видит агент: `kb-` и 12 знаков отпечатка. */
  version: string;
  /** Виды в порядке набора. */
  kinds: KbKind[];
  limits: KbLimits;
  /** Пустые разделы на языках писем, по языкам и видам. */
  gaps: KbGap[];
}

/** Запись с экрана: поля формы как есть, приводит и проверяет сервер. */
export interface KbEntryBody {
  kind: KbKind;
  language: string;
  title: string;
  text: string;
  tags: string[];
  active: boolean;
}

export interface FactCard {
  id: number;
  title: string;
  text: string;
  tags: string[];
}

/** Факты одного вида на одном языке. */
export interface FactGroup {
  kind: KbKind;
  language: string;
  facts: FactCard[];
}

/** Что увидит агент: только включённые записи, группами по виду и языку. */
export interface AgentView {
  version: string;
  total: number;
  groups: FactGroup[];
}

/** Поле отправителя продаж (`sender.FIELDS` сервера). */
export type SenderField =
  | 'sender_name'
  | 'sender_position'
  | 'signature'
  | 'website'
  | 'telegram'
  | 'physical_address'
  | 'call_link';

/** Отправитель целиком: пусто — «не задано». */
export type SenderBody = Record<SenderField, string | null>;

export interface SenderView extends SenderBody {
  updated_by: string | null;
  updated_at: string | null;
  /** Чего не хватает для отправки продаж — словами отказа. Пусто — готово. */
  missing: string[];
  /** Предел длины каждого поля. */
  limits: Record<SenderField, number>;
}

/** Язык письма цепочки (`chain_text.LANGUAGES` сервера). */
export type ChainLanguage = 'ru' | 'en';

/** Что сборка сделает с зоной письма: может переписать модель под адресата (в отдельном
 *  письме зона может остаться текстом шаблона — сборка подбирает зоны под коридор отличия)
 *  или отправляет как есть (`ZoneKind` шаблонов доноров). */
export type ZoneKind = 'rewrite' | 'fixed';

/** Подстановка шаблона цепочки (`chain_text.PLACEHOLDERS` сервера). */
export type ChainPlaceholder = 'name' | 'company' | 'site';

export interface ZoneCard {
  name: string;
  kind: ZoneKind;
  text: string;
}

/** Шаблон шага: чей набор, что в нём, включён ли, кто и когда правил. */
export interface ChainStepCard {
  id: number;
  /** Номер гипотезы; `null` — общий набор. */
  hypothesis_id: number | null;
  /** 1 — первое письмо, 2 и 3 — добивки. */
  step: number;
  language: string;
  /** Тема — только у первого письма: добивки идут в той же переписке. */
  subject: string | null;
  /** Тело в формате зон: `[имя] rewrite` / `[имя] fixed`. */
  body: string;
  zones: ZoneCard[];
  /** Коридор отличия у первого письма трудно достижим — словами сервера и с советом: шаг
   *  записан, экран показывает жёлтую строку у шага. `null` — предупреждения нет. */
  warning: string | null;
  active: boolean;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
}

/** Цепочка, которую получит лид набора на языке. */
export interface ChainState {
  language: string;
  /** `own` — своя цепочка гипотезы, `common` — общая. */
  source: 'own' | 'common';
  /** Каких шагов нет — словами отказа сборки («первой добивки»). Пусто — полна. */
  missing: string[];
  version: string;
}

/** Набор шаблонов — включённые и нет — и цепочки по языкам. */
export interface ChainView {
  hypothesis_id: number | null;
  rows: ChainStepCard[];
  chains: ChainState[];
  steps: number[];
  languages: string[];
  placeholders: string[];
  limits: { subject: number; body: number };
}

/** Шаблон шага для предпросмотра: ничего не пишет. */
export interface ChainPreviewBody {
  step: number;
  language: string;
  subject: string | null;
  body: string;
}

/** Шаг набора целиком: без гипотезы — общий набор. */
export interface ChainStepBody extends ChainPreviewBody {
  hypothesis_id: number | null;
  active: boolean;
}

/** Письмо глазами адресата: зоны с выдуманными значениями, подпись и адрес из
 *  настроек отправителя — в этом порядке их допишет сборка. */
export interface ChainPreviewView {
  /** У добивки — `null`: тему даёт первое письмо. */
  subject: string | null;
  zones: ZoneCard[];
  /** Какие выдуманные значения подставлены. */
  values: Record<string, string>;
  sender_name: string | null;
  signature: string | null;
  address: string | null;
  /** Чего не хватает для отправки продаж — словами сервера. */
  missing: string[];
  /** Коридор отличия трудно достижим — тем же правилом, что у записи шага; `null` — нет. */
  warning: string | null;
}

/** Очередь писем продаж гипотезы (`GET /sales/queue`): подключены ли продажи,
 *  цепочки по языкам, сколько лидов и писем ждёт. */
export interface SalesQueueView {
  hypothesis_id: number;
  connected: boolean;
  /** Чего не хватает продажам — словами отказа отправки. Пусто — подключены. */
  missing: string[];
  /** Цепочка гипотезы на каждом языке — той же формой, что у вкладки цепочки. */
  chains: ChainState[];
  /** Лидов «готов» без письма — их возьмёт следующая сборка. */
  unwritten: number;
  /** Писем гипотезы в очереди. */
  queued: number;
  /** Писем продаж в очереди — всех гипотез: пачка берёт очередь этапа целиком. */
  stage_queued: number;
  /** Больше писем за одну сборку сервер не примет. */
  limit_max: number;
  /** Больше писем одна пачка не возьмёт: окно подтверждения называет этот потолок. */
  batch_max: number;
  /** Автоотправка первых писем: включена ли, как часто, сколько в окне получателя, почему стоит. */
  autosend: SalesAutoLine;
  /** Автосборка очереди: хватает ли на сутки, кого соберёт или почему нет, последняя сама. */
  autobuild: SalesAutoLine;
}

/** Автоотправка или автосборка (`GET /sales/queue`): включена ли и что сейчас — строкой
 *  сервера, тем же снимком, каким судит проход. */
export interface SalesAutoLine {
  enabled: boolean;
  line: string;
}

/** Собрать очередь гипотезы: сколько писем за раз. */
export interface SalesQueueBody {
  hypothesis_id: number;
  limit: number;
}

/** Итог сборки — отчёт задачи (`QueueReport` сервера). */
export interface SalesQueueReport {
  campaign_id: number;
  prepared: number;
  /** Письма в очереди, собранные заново: сменилась цепочка, подпись или адрес. */
  refreshed: number;
  tokens_spent: number;
  off_corridor: number;
  /** Добивок писем в очереди, которые уйдут текстом шаблона: не легли в коридор отличия. У итога
   *  сборки прежней версии сервера поля нет. */
  followups_template: number;
  /** Почему лиды ждут: причина словами → сколько. */
  waiting: Record<string, number>;
  /** Сборка остановлена потолком расхода на модель — причина словами. */
  stopped: string | null;
  /** Почему зоны остались шаблонными: причина словами → сколько раз (как у сборки доноров). */
  notes: Record<string, number>;
  /** Письма, которые писала модель, — по порядку сборки. У итога сборки прежней версии
   *  сервера поля нет: экран читает его осторожно (`buildLetters`). */
  letters: SalesQueueLetter[];
}

/** Письмо сборки: лид, отличие, принятые зоны, вызовов модели, в очереди ли
 *  (`corridor.LetterLine` сервера). */
export interface SalesQueueLetter {
  lead_id: number;
  /** Имя лида; `null` — экран называет номер. */
  name: string | null;
  /** Вызовов модели: первый — всегда; ни один набор зон не лёг в коридор — сборка зовёт модель
   *  заново, до потолка настройки. */
  attempts: number;
  /** Отличие последней попытки — долей 0–1. */
  difference: number;
  /** То же отличие словами сервера — «21,3%»: правило числа у края коридора одно. */
  difference_text: string;
  /** Принятые переписанные зоны — остальные в этом письме текстом шаблона. */
  zones: string[];
  /** Письмо стоит в очереди; нет — лид ждёт. */
  queued: boolean;
  /** Почему письмо не встало в очередь — словами сервера, по этому письму: вне коридора — с
   *  отличием каждой попытки. `null` — встало. */
  reason: string | null;
  /** Добивки с зонами rewrite, собранные с письмом, — по порядку шагов. У итога сборки прежней
   *  версии сервера поля нет: экран читает его осторожно (`letterLine`). */
  followups: SalesQueueFollowup[];
}

/** Добивка письма сборки (`corridor.FollowupLine` сервера): шаг цепочки (2 или 3), вызовов
 *  модели, отличие — или почему уйдёт текстом шаблона. */
export interface SalesQueueFollowup {
  step: number;
  attempts: number;
  /** Отличие принятого текста долей; `null` — добивка уйдёт текстом шаблона. */
  difference: number | null;
  difference_text: string | null;
  /** Принятые переписанные зоны; остальные — текст шаблона. */
  zones: string[];
  /** Почему уйдёт текстом шаблона — словами сервера, с отличием каждой попытки; `null` —
   *  переписана. */
  reason: string | null;
}

/** Добивки лида, которые ещё уйдут (`GET /sales/leads/{номер}/followups`), — по порядку шагов. */
export interface SalesLeadFollowupsView {
  lead_id: number;
  followups: SalesFollowupCard[];
}

/** Добивка, какой она уйдёт: текст без подписи и адреса (их допишет отправка) и как он получен
 *  — словами сервера. */
export interface SalesFollowupCard {
  step: number;
  /** `null` — шага в цепочке нет, добивка ждёт. */
  body: string | null;
  words: string;
  difference: number | null;
  difference_text: string | null;
  zones: string[];
}
