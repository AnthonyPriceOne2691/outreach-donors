/**
 * Открытая переписка ведёт часть списка за собой: переписка продаж — в «Диалогах продаж»
 * (`?stage=sales`), остальные — в «Диалогах» (просьба Anthony 11.10.2026: продажи в меню —
 * своей группой).
 *
 * По ссылке без части — `/threads/42` из уведомления продаж или с карточки донора —
 * список рядом и подсветка меню стояли бы в другой части, а открытой строки в списке
 * не было бы. Часть в адресе поправляется заменой записи, а не новой: «назад» ведёт
 * туда, откуда пришли.
 */

import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';

import { partOf, withPart } from '../api/stages';
import type { Stage } from '../api/stages';
import { useSession } from '../auth/AuthProvider';

/** `stage` — этап открытой переписки; `undefined` — она ещё не пришла. */
export function useFollowThreadPart(stage: Stage | undefined): void {
  const [params, setParams] = useSearchParams();
  // Без права «Продажи» части продаж нет (П2): переписку продаж сервер и не отдаст.
  const sales = useSession().can('sales');
  const wanted = stage === 'sales' ? 'sales' : null;
  const stray = stage !== undefined && sales && partOf(params) !== wanted;
  useEffect(() => {
    if (stray) setParams((current) => withPart(current, wanted), { replace: true });
  }, [stray, wanted, setParams]);
}
