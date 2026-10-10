/**
 * Гипотезы раздела: ключ кэша списка и заведение новой из окна «Новая гипотеза».
 *
 * **Заведённая — в списке сразу**, а не после перезапроса: мастер загрузки выбирает её в
 * поле «Гипотеза» тем же движением, и поле не должно стоять пустым, пока едет список.
 * Карточка сервера — той же формы, что строка списка (лидов ноль в каждом состоянии), и
 * встаёт в конец: сервер отдаёт гипотезы старшими первыми. Затем список перечитывается —
 * правда остаётся за сервером.
 *
 * Своим модулем, а не в `SalesPage`: окно открывают и страница раздела, и мастер, а
 * ключ из страницы замкнул бы круг импортов «страница → окно → страница».
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';

import { addHypothesis } from '../api/sales';
import type { HypothesesView, HypothesisBody, HypothesisCard } from '../api/salesTypes';
import { notify } from '../notices';

export const HYPOTHESES_QUERY_KEY = ['sales', 'hypotheses'] as const;

/** Завести гипотезу; `onAdded` — что сделать с заведённой: закрыть окно, выбрать её. */
export function useAddHypothesis(onAdded: (card: HypothesisCard) => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: HypothesisBody) => addHypothesis(body),
    onSuccess: (card) => {
      client.setQueryData<HypothesesView>(HYPOTHESES_QUERY_KEY, (known) =>
        known === undefined ? known : { rows: [...known.rows, card], total: known.total + 1 },
      );
      void client.invalidateQueries({ queryKey: HYPOTHESES_QUERY_KEY });
      notify({ message: `Гипотеза «${card.name}» заведена`, color: 'green' });
      onAdded(card);
    },
  });
}
