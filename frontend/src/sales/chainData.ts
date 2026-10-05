/**
 * Запросы вкладки «Цепочка писем»: набор, запись шага, предпросмотр письма.
 *
 * Ключ кэша — набор: общий и каждая гипотеза читаются отдельно, и выбор набора
 * не показывает чужие шаблоны, пока едет свой. Запись шага перечитывает все
 * наборы разом: своя цепочка гипотезы заменяет общую на языке, и шаг, записанный
 * в общий набор, меняет то, что видит гипотеза без своих шагов.
 */

import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { previewChainStep, readChain, saveChainStep } from '../api/sales';
import { chainLanguageTitle, chainStepTitle } from '../api/salesLabels';
import type { ChainStepCard } from '../api/salesTypes';

export const CHAIN_QUERY_KEY = ['sales', 'chain'] as const;

/** Набор: `null` — общий, число — гипотеза. */
export function useChain(hypothesis: number | null) {
  return useQuery({
    queryKey: [...CHAIN_QUERY_KEY, hypothesis],
    queryFn: () => readChain(hypothesis),
  });
}

/** Записать шаг: ответ — уведомлением с шагом и языком, наборы перечитываются. */
export function useSaveStep(onSaved: (card: ChainStepCard) => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: saveChainStep,
    onSuccess: async (card) => {
      onSaved(card);
      await client.invalidateQueries({ queryKey: CHAIN_QUERY_KEY });
      const what = `${chainStepTitle(card.step)} · ${chainLanguageTitle(card.language)}`;
      notifications.show({ message: `Сохранено: ${what}`, color: 'green' });
    },
  });
}

/** Письмо глазами адресата — по нажатию, без записи: черновик окна, а не база. */
export function usePreview() {
  return useMutation({ mutationFn: previewChainStep });
}
