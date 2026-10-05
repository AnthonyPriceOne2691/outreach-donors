/**
 * Запись базы знаний в окне правки: черновик, отказ до нажатия, тело запроса.
 *
 * **Границы — те, что назвал сервер** (`limits` в ответе списка): второго
 * экземпляра чисел на экране нет, и правка предела на сервере не расходится
 * с экраном. До нажатия экран ловит только пустое и длинное — форму языка,
 * занятый заголовок и прочее судит сервер, и его отказ показывается словами.
 *
 * **Стёртое поле — «впишите», а не пустая запись**: пустая запись агенту
 * ничего не скажет, и сервер её всё равно не примет.
 */

import type { KbEntryBody, KbEntryCard, KbKind, KbLimits } from '../api/salesTypes';
import { formatNumber } from '../format';

export type KbDraft = KbEntryBody;

/** Поля, у которых бывает отказ до нажатия. */
export type KbDraftField = 'language' | 'title' | 'text' | 'tags';

/** Черновик записи: правка — от неё самой, новая — пустая первого вида. */
export function draftOf(entry: KbEntryCard | null, firstKind: KbKind): KbDraft {
  if (entry === null) {
    return { kind: firstKind, language: '', title: '', text: '', tags: [], active: true };
  }
  const { kind, language, title, text, tags, active } = entry;
  return { kind, language, title, text, tags, active };
}

function lengthRefusal(value: string, limit: number, empty: string): string | null {
  if (value.trim() === '') return empty;
  return value.length > limit
    ? `Длиннее ${formatNumber(limit)} знаков: сейчас ${formatNumber(value.length)}`
    : null;
}

function tagsRefusal(tags: string[], limits: KbLimits): string | null {
  if (tags.length > limits.tags) return `Тегов ${tags.length}, а можно не больше ${limits.tags}`;
  const long = tags.find((tag) => tag.length > limits.tag);
  return long === undefined ? null : `Тег длиннее ${limits.tag} знаков: «${long.slice(0, 24)}…»`;
}

/** Что не так с полями — словами, до отправки. Пусто — черновик можно слать. */
export function refusalsOf(
  draft: KbDraft,
  limits: KbLimits,
): Partial<Record<KbDraftField, string>> {
  const found: Partial<Record<KbDraftField, string | null>> = {
    language: draft.language.trim() === '' ? 'Впишите код языка: ru, en, pt-br' : null,
    title: lengthRefusal(draft.title, limits.title, 'Впишите заголовок — по нему запись узнают'),
    text: lengthRefusal(draft.text, limits.text, 'Впишите текст — из него пишет агент'),
    tags: tagsRefusal(draft.tags, limits),
  };
  return Object.fromEntries(Object.entries(found).filter(([, words]) => words !== null));
}

/** Изменилось ли что-нибудь против записи — без правки «Сохранить» не нужна. */
export function changed(draft: KbDraft, entry: KbEntryCard): boolean {
  const before = draftOf(entry, entry.kind);
  return JSON.stringify(draft) !== JSON.stringify(before);
}
