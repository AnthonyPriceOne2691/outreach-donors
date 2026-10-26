/**
 * Открытая переписка ведёт часть списка за собой: переписка продаж — в «Диалогах продаж»
 * (`?stage=sales`, просьба Anthony 11.10.2026), донора — в «Диалогах с донорами»
 * (`?stage=donors`), рекламодателя — в «Диалогах с рекламодателями» (`?stage=advertisers`,
 * 23.10.2026).
 *
 * По ссылке без части — `/threads/42` из уведомлений Telegram и Kommo или с карточки
 * донора — список рядом, заголовок и подсветка меню стояли бы не в той части, а открытой
 * строки в списке могло не быть. Часть в адресе поправляется заменой записи, а не новой:
 * «назад» ведёт туда, откуда пришли. «Кому писали» (`?role=`) при этом уходит: в части
 * он ничего не сужает.
 */

import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';

import { partOf, stageShown, withPart } from '../api/stages';
import type { Stage } from '../api/stages';
import { useSession } from '../auth/AuthProvider';
import { ROLE_PARAM } from './threadFilters';

/** `stage` — этап открытой переписки; `undefined` — она ещё не пришла. */
export function useFollowThreadPart(stage: Stage | undefined): void {
  const [params, setParams] = useSearchParams();
  // Без права «Продажи» части продаж нет (П2): переписку продаж сервер и не отдаст.
  const sales = useSession().can('sales');
  const wanted = stage !== undefined && stageShown(stage, sales) ? stage : null;
  const stray = wanted !== null && partOf(params) !== wanted;
  useEffect(() => {
    if (!stray) return;
    setParams(
      (current) => {
        const next = withPart(current, wanted);
        next.delete(ROLE_PARAM);
        return next;
      },
      { replace: true },
    );
  }, [stray, wanted, setParams]);
}
