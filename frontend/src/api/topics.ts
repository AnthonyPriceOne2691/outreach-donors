/**
 * Тематика сайта в ответах сервера: тема строки, пункты фильтра, токены тематики в смете.
 *
 * Поля темы — дополнением интерфейсов `types.ts` отсюда: `types.ts` у потолка длины
 * (снимок + 30 строк), а тема нужна строке доноров, кандидату рассмотрения, странице
 * доноров и смете. Подписи тем — у сервера (`backend/features/topics/taxonomy.py`): своей
 * копии у экрана нет, он показывает присланное. Поля необязательные: ответ сервера без
 * тематики их не знает.
 */

/** Тема строки: код — для фильтра и адреса, подпись — для глаза. */
export interface TopicMark {
  code: string;
  label: string;
}

/** Пункт фильтра «Тематика»: у скольких строк экрана тема основная или вторая. */
export interface TopicCount extends TopicMark {
  count: number;
}

/** Тема у строки: основная, вторая и почему — фразой модели. */
export interface TopicFields {
  topic?: TopicMark | null;
  topic_secondary?: TopicMark | null;
  topic_reason?: string | null;
}

/** Код пункта «тема не определена» — в фильтре и в адресе (`topics/taxonomy.NOT_DEFINED`). */
export const NOT_DEFINED = 'none';

declare module './types' {
  interface DonorRowCard {
    topic?: TopicMark | null;
    topic_secondary?: TopicMark | null;
    topic_reason?: string | null;
  }
  interface ReviewCandidate {
    topic?: TopicMark | null;
    topic_secondary?: TopicMark | null;
    topic_reason?: string | null;
  }
  interface DonorsPage {
    /** Пункты фильтра «Тематика» — по всем донорам экрана, тем же условием, что фильтр. */
    topics?: TopicCount[];
  }
  interface Forecast {
    /** Токены модели на тематику сайтов — худший случай; ноль — темы прогон не определит. */
    topic_tokens?: number;
    topic_tokens_per_site?: number;
    /** По замеру прошлых прогонов, а не оценка по размеру промпта. */
    topic_measured?: boolean;
  }
}
