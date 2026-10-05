/**
 * Что отдаёт сервер разделу «Продажи»: гипотезы, лиды, загрузка базы.
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

/** Откуда лид: файл или таблица — или коллега, названный в ответе. */
export type LeadSource = 'import' | 'referral';

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
}

export interface HypothesesView {
  rows: HypothesisCard[];
  total: number;
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

/** Что сборка сделает с зоной письма: переписывает модель под адресата или
 *  отправляет как есть (`ZoneKind` шаблонов доноров). */
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
}
