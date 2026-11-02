/**
 * Блок «Рассылка» панели: то же, что вкладка «Рассылка» в продажах, для рекламодателей — почему
 * письма не уходят, уходит ли очередь сама, когда она уйдёт, что с письмами, факт и ящики.
 *
 * **Экран своего счёта не ведёт, и блоки те же.** Прогноз — общая ручка почты
 * (`GET /letters/forecast?stage=advertisers`), блоки вкладки продаж принимают её ответ как есть:
 * вторая копия разошлась бы с ними на первой правке. Режим — строкой очереди экрана писем
 * (`QueueAutosendLine`), части — счётчиками списка рекламодателей (`/advertisers/list/facets`):
 * каждый рекламодатель — ровно в одной, той же, что в колонке «Письмо».
 *
 * **Плана по дням здесь пока нет**: подсказка блока плана говорит о ящиках продаж и лидах, а его
 * правят в разделе продаж. Подключается одной строкой, когда слова станут общими.
 *
 * **Отказ одной ручки — в её блоке**, остальные живут: как на вкладке продаж.
 */

import { Card, Group, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';

import { fetchAdvertiserForecast, fetchPanelMailing } from '../api/advertiserPanel';
import type { PanelMailing as PanelMailingModes } from '../api/advertiserPanel';
import { fetchAdvertiserFacets } from '../api/advertiserList';
import type { AdvertiserFacets } from '../api/advertiserList';
import { useSession } from '../auth/AuthProvider';
import { Metric } from '../components/Metric';
import { capitalized, formatNumber } from '../format';
import { QueueAutosendLine } from '../letters/QueueAutosend';
import { Block, NoAnswer, NotRefreshed } from '../sales/MailingBlock';
import { MailingBoxes } from '../sales/MailingBoxes';
import { MailingPast } from '../sales/MailingDays';
import { MailingBlocked, MailingQueue } from '../sales/MailingQueue';
import { MAILING_REFRESH_MS } from '../sales/mailingData';
import type { MailingForecastView } from '../sales/mailingTypes';
import { ADVERTISER_FACETS_KEY } from './AdvertiserList';
import { OFFER_KEYS, OFFER_STATES } from './listFilters';

const FORECAST_KEY = ['letters', 'forecast', 'advertisers', 'panel'] as const;
const MODES_KEY = ['advertisers', 'panel', 'mailing'] as const;

/** Уходит ли очередь сама — у ссылок и у бизнесов ниши: очереди у них разные. */
function Modes({ query }: { query: UseQueryResult<PanelMailingModes> }) {
  const { can } = useSession();
  const client = useQueryClient();
  const view = query.data;
  if (view === undefined) {
    return (
      <NoAnswer query={query} failed="Режим очереди не загрузился" loading="Загружаем режим" />
    );
  }
  const changed = () => void client.invalidateQueries({ queryKey: MODES_KEY });
  const running = view.links.running || view.niche.running;
  const stopped = view.links.stopped_at !== null || view.niche.stopped_at !== null;
  return (
    <Stack gap="xs" px="md" className="mailingMode">
      <NotRefreshed query={query} />
      {running || stopped ? null : (
        <Text size="sm" c="dimmed">
          Очередь сама не уходит: письма отправляют кнопкой на экране писем.
        </Text>
      )}
      <QueueAutosendLine
        stage="advertisers"
        autosend={view.links}
        canStop={can('send')}
        onChanged={changed}
      />
      <QueueAutosendLine
        stage="advertisers"
        audience="niche"
        autosend={view.niche}
        canStop={can('send')}
        onChanged={changed}
      />
    </Stack>
  );
}

/** Рекламодатели по слову оффера: каждый — ровно в одной части; нулевых частей нет. */
function Parts({ query }: { query: UseQueryResult<AdvertiserFacets> }) {
  const facets = query.data;
  if (facets === undefined) {
    return (
      <NoAnswer query={query} failed="Части не загрузились" loading="Загружаем рекламодателей" />
    );
  }
  const shown = OFFER_KEYS.filter((key) => (facets.letters[key] ?? 0) > 0);
  return (
    <Block title="Рекламодатели по письмам" className="panelParts">
      {shown.length === 0 ? (
        <Text size="sm" c="dimmed" px="md">
          Рекламодателей пока нет: их заводит перевод с вкладки «Проверка».
        </Text>
      ) : (
        <SimpleGrid cols={{ base: 2, sm: 4, lg: Math.max(4, shown.length) }} spacing="sm" px="md">
          {shown.map((key) => (
            <Metric
              key={key}
              title={capitalized(OFFER_STATES[key].title)}
              value={formatNumber(facets.letters[key] ?? 0)}
            />
          ))}
        </SimpleGrid>
      )}
    </Block>
  );
}

/** Ящиков нет — своими словами: блок вкладки продаж говорит о своих доменах. */
function Boxes({ view }: { view: MailingForecastView }) {
  if (view.senders.length > 0) return <MailingBoxes view={view} />;
  return (
    <Block title="Ящики и разгон" className="mailingBoxesBlock">
      <Text size="sm" c="dimmed" px="md">
        Ящиков этапа нет — первые письма не уйдут. Ящики заводят на экране «Домены рассылки».
      </Text>
    </Block>
  );
}

export function PanelMailing() {
  const forecast = useQuery({
    queryKey: FORECAST_KEY,
    queryFn: fetchAdvertiserForecast,
    refetchInterval: MAILING_REFRESH_MS,
  });
  const modes = useQuery({
    queryKey: MODES_KEY,
    queryFn: fetchPanelMailing,
    refetchInterval: MAILING_REFRESH_MS,
  });
  const facets = useQuery({ queryKey: ADVERTISER_FACETS_KEY, queryFn: fetchAdvertiserFacets });
  const view = forecast.data;
  return (
    <Card className="glassPanel" p="md">
      <Stack gap="lg">
        <Group px="md">
          <Title order={3}>Рассылка рекламодателям</Title>
        </Group>
        <MailingBlocked view={view} />
        <Modes query={modes} />
        <MailingQueue query={forecast} />
        <Parts query={facets} />
        {view === undefined ? null : (
          <>
            <MailingPast view={view} />
            <Boxes view={view} />
          </>
        )}
      </Stack>
    </Card>
  );
}
