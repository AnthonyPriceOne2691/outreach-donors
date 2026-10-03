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
