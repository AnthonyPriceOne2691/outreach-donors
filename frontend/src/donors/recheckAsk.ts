/**
 * Нажатие «Перезапросить цену» — одно на экран и выше окна (проверка 12.10.2026).
 *
 * Окно с кнопками закрывают, пока письма уходят, и открывают снова. Живи нажатие внутри
 * окна, оно ушло бы вместе с ним, а открытое заново окно показало бы кнопки свободными:
 * второе нажатие ушло бы поверх первого. Поэтому нажатие заводит тот, кто держит окно, —
 * панель обхода (`StalePrices`) и карточка донора (`PriceRecheck`), — и отдаёт его окну:
 * кнопки заперты до итога, а итог — в окне, словами сервера.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { QueryKey, UseMutationResult } from '@tanstack/react-query';

import type { RecheckResult } from '../api/priceRecheck';
import { askPrices } from '../api/priceRecheck';

/** Нажатие: номера доноров — итог сервера. */
export type RecheckAsking = UseMutationResult<RecheckResult, Error, number[]>;

/** Нажатие «перезапросить» у того, кто держит окно. `stale` — что перечитать, когда письма
 *  ушли, по порядку; `onAsked` — что сделать до этого (панели — перечитать доноров). */
export function useRecheckAsk(stale: readonly QueryKey[], onAsked?: () => void): RecheckAsking {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (donorIds: number[]) => askPrices(donorIds),
    onSuccess: async () => {
      onAsked?.();
      for (const queryKey of stale) await queryClient.invalidateQueries({ queryKey });
    },
  });
}
