/**
 * Решение человека на «Отборе» — и место решённой строки.
 *
 * **Решённая строка стоит, где стояла** (проверка QA 10.10.2026). «Продаёт
 * своё» и «Не продаёт места» уводят домен на «Отклонены», и после решения
 * список перечитывался: строка пропадала, нижние поднимались под курсор,
 * и следующее нажатие в том же месте приходилось на соседний домен — при
 * проверке решили kitchen-daily вместо home-guide. Уведомление называло
 * домен, ошибку было видно, но ловушка оставалась.
 *
 * Список перечитывается по-прежнему — числа вкладок и сводка верны сразу, —
 * а строки стоят в том порядке, в каком их видел человек: ушедшая — на своём
 * месте, с пометкой «решение → вкладка» и «Вернуть» (`SelectionRow`);
 * пришедшая с соседней страницы — в конце. Порядок держится до смены
 * вкладки, фильтра или страницы: там список другой, и чужим строкам в нём
 * не место.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { HUMAN_INTENTS } from '../api/labels';
import { decideSite } from '../api/selection';
import type { HumanIntent, SelectionCard } from '../api/types';
import { notify } from '../notices';

export const SELECTION_KEY = ['selection'] as const;

/** Ушедшая строка — чем держать её высоту: под кнопками стоял значок «разошёлся
 *  с судьёй», и без его места строка сжималась бы, а нижние поднимались под курсор. */
export interface GoneRow {
  disagreed: boolean;
}

/** Строка, решённая на этом виде: как её вернул сервер и что было до. */
interface Held extends GoneRow {
  row: SelectionCard;
  /** Решение до первого нажатия на этом виде — его возвращает «Вернуть». */
  was: HumanIntent | null;
}

/** Что держит строки на месте: вид, порядок строк в миг решения и решённые. */
export interface Hold {
  /** Вкладка, фильтры и страница — другой вид, другой список. */
  view: string;
  order: number[];
  held: ReadonlyMap<number, Held>;
}

export interface Arranged {
  rows: SelectionCard[];
  /** Ушли решением с вкладки, но стоят на месте до смены вида. */
  gone: ReadonlyMap<number, GoneRow>;
}

/** Нажатие: строка, решение, а ещё вид и порядок строк в миг нажатия. */
interface Asked {
  row: SelectionCard;
  intent: HumanIntent | null;
  view: string;
  order: number[];
}

export function blankHold(view: string): Hold {
  return { view, order: [], held: new Map() };
}

/**
 * Строки на экран. Решений на этом виде не было — как пришли с сервера.
 * Были — в прежнем порядке: строка, что ещё на вкладке, — свежей, ушедшая —
 * какой её вернуло решение; строки, которых человек не видел (с соседней
 * страницы), — в конце, а не под курсором.
 */
export function arrange(fresh: SelectionCard[], hold: Hold): Arranged {
  const gone = new Map<number, GoneRow>();
  if (hold.order.length === 0) return { rows: fresh, gone };
  const byId = new Map(fresh.map((row) => [row.domain_id, row]));
  const rows: SelectionCard[] = [];
  for (const id of hold.order) {
    const here = byId.get(id);
    const left = hold.held.get(id);
    if (here !== undefined) rows.push(here);
    else if (left !== undefined) {
      rows.push(left.row);
      gone.set(id, { disagreed: left.disagreed });
    }
  }
  const seen = new Set(hold.order);
  return { rows: [...rows, ...fresh.filter((row) => !seen.has(row.domain_id))], gone };
}

/** Запомнить решение. «До» — первое на этом виде: «Вернуть» после двух
 *  решений подряд возвращает то, что было до первого. */
export function remember(hold: Hold, asked: Asked, after: SelectionCard): Hold {
  const base = hold.view === asked.view ? hold : blankHold(asked.view);
  const held = new Map(base.held);
  // Не `??`: «до» бывает и «не смотрел» (`null`), и его второе решение не
  // должно подменять тем, что выбрали первым.
  const earlier = base.held.get(after.domain_id);
  const was = earlier === undefined ? asked.row.human.intent : earlier.was;
  held.set(after.domain_id, { row: after, was, disagreed: asked.row.disagrees });
  return { view: asked.view, order: asked.order, held };
}

/** Решения на виде `view` над строками `fresh`, как их отдал сервер. */
export function useDecisions(view: string, fresh: SelectionCard[]) {
  const queryClient = useQueryClient();
  const [hold, setHold] = useState<Hold>(() => blankHold(view));
  // Другой вид — решённые прежнего забыты, а не отложены: вернувшись на ту же
  // страницу, человек видит её свежей, без строк, ушедших ещё тогда.
  if (hold.view !== view) setHold(blankHold(view));
  const current = hold.view === view ? hold : blankHold(view);
  const arranged = arrange(fresh, current);

  const save = useMutation({
    mutationFn: ({ row, intent }: Asked) => decideSite(row.domain_id, intent),
    onSuccess: async (after, asked) => {
      // Сначала — запомнить место, потом перечитать: пришедший список уже
      // без ушедшей строки, и держать её надо с первого же кадра.
      setHold((was) => remember(was, asked, after));
      await queryClient.invalidateQueries({ queryKey: SELECTION_KEY });
      const said =
        after.human.intent === null ? 'решение снято' : HUMAN_INTENTS[after.human.intent];
      notify({ message: `${after.host}: ${said}`, color: 'green' });
    },
    onError: (failure) =>
      notify({ title: 'Не записали', message: refusalOf(failure), color: 'red' }),
  });

  const decide = (row: SelectionCard, intent: HumanIntent | null) =>
    save.mutate({ row, intent, view, order: arranged.rows.map((one) => one.domain_id) });

  return {
    ...arranged,
    decide,
    /** «Вернуть»: решение, которое было до первого нажатия на этом виде. */
    undo: (row: SelectionCard) => decide(row, current.held.get(row.domain_id)?.was ?? null),
    /** Домен, решение по которому сейчас уходит на сервер. */
    deciding: save.isPending ? (save.variables?.row.domain_id ?? null) : null,
  };
}
