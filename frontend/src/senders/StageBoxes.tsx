/**
 * «Ящики и разгон» этапа — под его доменами: ступень сейчас, следующая ступень и почему ящик сегодня
 * не берёт писем. На карточке домена этого нет: там итог домена и кнопка.
 *
 * **Блок один на все этапы** (`MailingBoxes`, общий прогноз почты). С панели рекламодателей и вкладки
 * «Рассылка» продаж он переехал сюда (просьба Anthony 05.11.2026): ящики смотрят там же, где домены.
 * Ящиков нет — блока нет: об этом уже сказала строка экрана.
 *
 * **Отказ прогноза — в этом блоке**, домены выше живут: ручки разные.
 */

import { Card } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { fetchStageBoxes } from '../api/senders';
import type { Stage } from '../api/stages';
import { NoAnswer, NotRefreshed } from '../components/MailingBlock';
import { MailingBoxes } from './MailingBoxes';

/** Перечитывается раз в минуту, как прогноз рассылки на вкладках этапов. */
const BOXES_REFRESH_MS = 60_000;

export function StageBoxes({ stage }: { stage: Stage }) {
  const forecast = useQuery({
    queryKey: ['senders', 'boxes', stage],
    queryFn: () => fetchStageBoxes(stage),
    refetchInterval: BOXES_REFRESH_MS,
  });
  const view = forecast.data;
  if (view !== undefined && view.senders.length === 0) return null;
  return (
    <Card className="glassPanel" p="md">
      {view === undefined ? (
        <NoAnswer query={forecast} failed="Ящики не загрузились" loading="Загружаем ящики" />
      ) : (
        <>
          <NotRefreshed query={forecast} />
          <MailingBoxes view={view} />
        </>
      )}
    </Card>
  );
}
