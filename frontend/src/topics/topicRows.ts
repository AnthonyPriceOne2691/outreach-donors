/**
 * Тематика строк, пришедших целиком, — очередь рассмотрения прогона.
 *
 * У «Доноров» пункты фильтра и сам фильтр считает сервер: таблица — по страницам. Очередь
 * прогона приходит одним ответом на вкладку, и её строки фильтруются здесь — тем же
 * правилом, что у сервера (`topics/store.topic_is`): тема основная или вторая, «не
 * определена» — темы нет. Счётчики пунктов — по тем же строкам, что список.
 */

import { useMemo, useState } from 'react';

import { NOT_DEFINED } from '../api/topics';
import type { TopicCount, TopicFields } from '../api/topics';

/** Подпись пункта «тема не определена» — та же, что у сервера (`api/topics/schemas.py`). */
export const NOT_DEFINED_LABEL = 'не определена';

/** Есть ли у строки тема `code` — основная или вторая; `none` — темы нет. */
export function hasTopic(row: TopicFields, code: string): boolean {
  if (code === NOT_DEFINED) return !row.topic;
  return row.topic?.code === code || row.topic_secondary?.code === code;
}

/** Пункты фильтра по строкам: частые сверху, поровну — по подписи, «не определена» — последней. */
export function topicsOf(rows: readonly TopicFields[]): TopicCount[] {
  const found = new Map<string, TopicCount>();
  let missing = 0;
  for (const row of rows) {
    if (!row.topic) missing += 1;
    for (const mark of [row.topic, row.topic_secondary]) {
      if (!mark) continue;
      const was = found.get(mark.code);
      found.set(mark.code, { ...mark, count: (was?.count ?? 0) + 1 });
    }
  }
  const items = [...found.values()].sort(
    (a, b) => b.count - a.count || a.label.localeCompare(b.label, 'ru'),
  );
  return missing > 0
    ? [...items, { code: NOT_DEFINED, label: NOT_DEFINED_LABEL, count: missing }]
    : items;
}

/** Есть ли что фильтровать: хоть у одной строки тема — иначе фильтр не нужен. */
export function anyTopic(items: readonly TopicCount[]): boolean {
  return items.some((item) => item.code !== NOT_DEFINED);
}

/** Тематика словами — для пустого списка: «тематика «Финансы»». */
export function topicWords(code: string, items: readonly TopicCount[]): string {
  const label = items.find((item) => item.code === code)?.label;
  return code === NOT_DEFINED ? 'тематика не определена' : `тематика «${label ?? code}»`;
}

/** Фильтр «Тематика» над строками, пришедшими целиком: выбранная тема, пункты и строки. */
export function useTopicFilter<T extends TopicFields>(rows: readonly T[] | undefined) {
  const [topic, setTopic] = useState<string | null>(null);
  const all = useMemo(() => rows ?? [], [rows]);
  const items = useMemo(() => topicsOf(all), [all]);
  const shown = useMemo(
    () => (topic === null ? all : all.filter((row) => hasTopic(row, topic))),
    [all, topic],
  );
  return { topic, setTopic, items, shown };
}
