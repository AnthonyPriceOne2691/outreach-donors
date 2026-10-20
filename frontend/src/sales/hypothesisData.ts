/**
 * Гипотезы раздела: ключ кэша списка, заведение новой из окна «Новая гипотеза», правка и
 * удаление из окна гипотезы.
 *
 * **Заведённая — в списке сразу**, а не после перезапроса: мастер загрузки выбирает её в
 * поле «Гипотеза» тем же движением, и поле не должно стоять пустым, пока едет список.
 * Карточка сервера — той же формы, что строка списка (лидов ноль в каждом состоянии), и
 * встаёт в конец: сервер отдаёт гипотезы старшими первыми. Затем список перечитывается —
 * правда остаётся за сервером.
 *
 * **Правка и удаление перечитывают раздел целиком** (`['sales']`): имя гипотезы стоит в
 * списках всех вкладок, а описание сегмента — на «Сборе базы», где смета годна только для
 * того описания, по которому её считали (`SegmentForm`): новое описание её снимает.
 *
 * **Что держит гипотезу, окно читает свежим** (`gcTime: 0`) — открыта ли «Удалить» и почему
 * нет, словами сервера; сервер всё равно считает заново под блокировкой и отвечает 409 словами.
 * Отказ удаления перечитывает и это: кнопка закрывается с новыми словами.
 *
 * Своим модулем, а не в `SalesPage`: окно открывают и страница раздела, и мастер, а
 * ключ из страницы замкнул бы круг импортов «страница → окно → страница». Запросы правки и
 * удаления и их типы — здесь же, а не в общих `api/sales.ts` и `api/salesTypes.ts`.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { request } from '../api/client';
import { addHypothesis } from '../api/sales';
import type { HypothesesView, HypothesisBody, HypothesisCard } from '../api/salesTypes';
import { notify } from '../notices';

export const HYPOTHESES_QUERY_KEY = ['sales', 'hypotheses'] as const;
/** Весь раздел: имя гипотезы — во всех вкладках, описание — в смете сбора. */
const SALES_QUERY_KEY = ['sales'] as const;
const HOLDS_QUERY_KEY = ['sales', 'hypothesis-holds'] as const;

/** «Компании из других стран»: решает человек (так у новой гипотезы) — или подходят. */
export type OtherCountries = 'review' | 'accept';

/** Поле гипотезы «страны компаний» (`hypothesis_countries` на сервере): переключатель и коды
 *  ISO «не пишем компаниям из». Сервер шлёт его в карточке гипотезы; в общем типе карточки
 *  (`api/salesTypes.ts`) его нет — тип здесь, рядом с окном. */
export interface CountriesField {
  other_countries: OtherCountries;
  excluded_countries: string[];
}

/** Поле карточки — или умолчание новой гипотезы («на проверку», пусто), если сервер его не прислал. */
export function countriesOf(card: HypothesisCard | undefined): CountriesField {
  const found = card as (HypothesisCard & Partial<CountriesField>) | undefined;
  return {
    other_countries: found?.other_countries ?? 'review',
    excluded_countries: found?.excluded_countries ?? [],
  };
}

/** Правка гипотезы — только то, что поправили: нет поля — оно не меняется. */
export interface HypothesisEdit extends Partial<CountriesField> {
  name?: string;
  /** Пустое — описания нет. */
  description?: string;
}

export interface HypothesisEdited extends CountriesField {
  hypothesis_id: number;
  name: string;
  description: string | null;
}

/** Что держит гипотезу: вид → сколько (только ненулевые) и строка под закрытой кнопкой. */
export interface HypothesisHolds {
  hypothesis_id: number;
  holds: Record<string, number>;
  /** Пусто — гипотеза пустая и удаляется. */
  held: string | null;
}

export function editHypothesis(id: number, body: HypothesisEdit): Promise<HypothesisEdited> {
  return request<HypothesisEdited>(`/sales/hypotheses/${id}`, { method: 'PATCH', body });
}

/** Коды стран, которые примет «Не пишем компаниям из» (таблица стран сервера). */
export function readCountryCodes(): Promise<{ codes: string[] }> {
  return request<{ codes: string[] }>('/sales/countries');
}

/** Коды стран — один раз за жизнь страницы и только когда выбор стран открыли (`enabled`). */
export function useCountryCodes(enabled: boolean) {
  return useQuery({
    queryKey: ['sales', 'countries'],
    queryFn: readCountryCodes,
    enabled,
    staleTime: Infinity,
  });
}

export function readHolds(id: number): Promise<HypothesisHolds> {
  return request<HypothesisHolds>(`/sales/hypotheses/${id}/holds`);
}

export function removeHypothesis(id: number): Promise<void> {
  return request<void>(`/sales/hypotheses/${id}`, { method: 'DELETE' });
}

/** Завести гипотезу; `onAdded` — что сделать с заведённой: закрыть окно, выбрать её. */
export function useAddHypothesis(onAdded: (card: HypothesisCard) => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: HypothesisBody) => addHypothesis(body),
    onSuccess: (card) => {
      client.setQueryData<HypothesesView>(HYPOTHESES_QUERY_KEY, (known) =>
        known === undefined
          ? known
          : { ...known, rows: [...known.rows, card], total: known.total + 1 },
      );
      void client.invalidateQueries({ queryKey: HYPOTHESES_QUERY_KEY });
      notify({ message: `Гипотеза «${card.name}» заведена`, color: 'green' });
      onAdded(card);
    },
  });
}

/** Сохранить правку; `onSaved` — закрыть окно. */
export function useEditHypothesis(onSaved: (edited: HypothesisEdited) => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: HypothesisEdit }) => editHypothesis(id, body),
    onSuccess: (edited) => {
      void client.invalidateQueries({ queryKey: SALES_QUERY_KEY });
      notify({ message: `Гипотеза «${edited.name}» сохранена`, color: 'green' });
      onSaved(edited);
    },
  });
}

/** Что держит гипотезу — свежим, на каждое открытие окна. */
export function useHolds(id: number) {
  return useQuery({
    queryKey: [...HOLDS_QUERY_KEY, id],
    queryFn: () => readHolds(id),
    gcTime: 0,
  });
}

/** Удалить пустую гипотезу; `onRemoved` — закрыть окно и снять её из адреса раздела. */
export function useRemoveHypothesis(onRemoved: (card: HypothesisCard) => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (card: HypothesisCard) => removeHypothesis(card.id),
    onSuccess: (_, card) => {
      onRemoved(card);
      void client.invalidateQueries({ queryKey: SALES_QUERY_KEY });
      notify({ message: `Гипотеза «${card.name}» удалена`, color: 'yellow' });
    },
    // Отказ — гипотезу держат или строку держит другая операция: что держит, перечитать.
    onError: () => void client.invalidateQueries({ queryKey: HOLDS_QUERY_KEY }),
  });
}
