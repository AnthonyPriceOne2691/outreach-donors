/**
 * Числа ждущей работы у пунктов меню (аудит экранов 09.10.2026).
 *
 * До них что ждёт человека, было видно только на «Обзоре»: с экрана писем не
 * узнать, что в «Диалогах» ответ ждёт разбора цены. Теперь число стоит у пункта
 * раздела — теми же правилами, что плитки «Ждут человека» (`GET /overview/work`).
 *
 * Спрашиваются раз в минуту и при каждом переходе: разобрали очередь, ушли в
 * другой раздел — у пункта уже новое число, а не через минуту. Не пришли —
 * чисел нет: меню не место для отказа, о нём скажет «Обзор».
 */

import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';

import { fetchWork } from '../api/overview';
import type { WorkView } from '../api/overview';

/** Под ключом сводки: перечитали сводку — перечитались и числа меню. */
export const WORK_QUERY_KEY = ['overview', 'work'] as const;

export type WorkSection = keyof WorkView;

export function useWork(enabled: boolean): WorkView | undefined {
  const { pathname } = useLocation();
  const { data, refetch } = useQuery({
    queryKey: WORK_QUERY_KEY,
    queryFn: fetchWork,
    enabled,
    refetchInterval: 60 * 1000,
  });
  const seen = useRef(pathname);
  useEffect(() => {
    if (!enabled || seen.current === pathname) return;
    seen.current = pathname;
    void refetch();
  }, [enabled, pathname, refetch]);
  return data;
}
