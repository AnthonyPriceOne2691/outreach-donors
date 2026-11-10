/**
 * Блок «Рассылка» панели: то же, что вкладка «Рассылка» в продажах, для рекламодателей — почему
 * письма не уходят, уходит ли очередь сама, когда она уйдёт, что с письмами и факт. Ящики и разгон —
 * на своём экране, пункт меню «Домены рассылки» (`senders/StageBoxes`, просьба Anthony 05.11.2026).
 *
 * **Экран своего счёта не ведёт, и блоки те же.** Прогноз — общая ручка почты
 * (`GET /letters/forecast?stage=advertisers`), блоки вкладки продаж принимают её ответ как есть:
 * вторая копия разошлась бы с ними на первой правке. Режим — строкой очереди экрана писем
 * (`QueueAutosendLine`), части — счётчиками списка рекламодателей (`/advertisers/list/facets`):
 * каждый рекламодатель — ровно в одной, той же, что в колонке «Письмо».
 *
 * **План и факт — блок вкладки продаж со словами этапа** (`MailingDaysTabs`, `PlanWords`): те же
 * вкладки, в подсказке — ящики рекламодателей и письма их очереди, а разгон, окно, добивки и дни по
 * UTC — общие правила прогноза, одним текстом в `sales/MailingDays.tsx`. Лидов, которым напишет
 * сборка, у рекламодателей нет.
 *
 * **Отказ одной ручки — в её блоке**, остальные живут: как на вкладке продаж.
 *
 * **Строка режима — с подписью своей очереди** (`AUDIENCE_NAMES`, словами переключателя «Писем»):
 * две «Сама больше не уходит с …» без подписи читались дублем (разведка 10.11.2026).
 *
 * **Пустая очередь — одной строкой** (разведка 10.11.2026): «очередь кончилась» в двух строках
 * режима, «В очереди 0» и «очередь пуста — отправлять нечего» в блоке срока говорили одно и то же.
 * Пока в очереди этапа нет писем, вместо них — «Очередь пуста» и ссылка туда, где письма собирают и
 * отправляют; очередь, которая ещё идёт, остаётся строкой с «Остановить». Части и план — как были.
 */

import { Anchor, Card, Group, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { fetchAdvertiserForecast, fetchPanelMailing } from '../api/advertiserPanel';
import type { PanelMailing as PanelMailingModes } from '../api/advertiserPanel';
import { fetchAdvertiserFacets } from '../api/advertiserList';
import type { AdvertiserFacets } from '../api/advertiserList';
import type { LetterAudience } from '../api/letters';
import { useSession } from '../auth/AuthProvider';
import { Metric } from '../components/Metric';
import { capitalized, formatNumber } from '../format';
import { QueueAutosendLine } from '../letters/QueueAutosend';
import { AUDIENCE_NAMES } from '../letters/targets';
import { Block, NoAnswer, NotRefreshed } from '../components/MailingBlock';
import { MailingDaysTabs } from '../sales/MailingDays';
import type { PlanWords } from '../sales/MailingDays';
import { MailingBlocked, MailingQueue } from '../sales/MailingQueue';
import { MAILING_REFRESH_MS } from '../sales/mailingData';
import type { MailingForecastView } from '../sales/mailingTypes';
import { ADVERTISER_FACETS_KEY } from './AdvertiserList';
import { OFFER_KEYS, OFFER_STATES } from './listFilters';

const FORECAST_KEY = ['letters', 'forecast', 'advertisers', 'panel'] as const;
/** Слова этапа в подсказке плана: чьи ящики и что ждёт отправки. */
const PLAN_WORDS: PlanWords = {
  boxes: 'ящики рекламодателей',
  supply: 'письма в очереди рекламодателям',
};
const MODES_KEY = ['advertisers', 'panel', 'mailing'] as const;
/** Очереди рекламодателей — по порядку переключателя «Писем». */
const AUDIENCES: LetterAudience[] = ['links', 'niche'];

/** Уходит ли очередь сама — у ссылок и у бизнесов ниши: очереди у них разные, и строка каждой —
 *  со своей подписью. Очередь этапа пуста (`empty`) — строк «сама больше не уходит» нет: что она
 *  кончилась, говорит строка пустой очереди; идущая остаётся — у неё «Остановить». Сказать
 *  нечего — блока нет. */
function Modes({ query, empty }: { query: UseQueryResult<PanelMailingModes>; empty: boolean }) {
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
  const shown = AUDIENCES.filter((audience) => !empty || view[audience].running);
  if (shown.length === 0 && !query.isRefetchError) return null;
  return (
    <Stack gap="xs" px="md" className="mailingMode">
      <NotRefreshed query={query} />
      {running || stopped || empty ? null : (
        <Text size="sm" c="dimmed">
          Очередь сама не уходит: письма отправляют кнопкой на экране писем.
        </Text>
      )}
      {shown.map((audience) => (
        <QueueAutosendLine
          key={audience}
          stage="advertisers"
          audience={audience}
          label={AUDIENCE_NAMES[audience]}
          autosend={view[audience]}
          canStop={can('send')}
          onChanged={changed}
        />
      ))}
    </Stack>
  );
}

/** Очередь этапа пуста — одна строка вместо блока срока: что пусто и где письма собирают. */
function EmptyQueue({ query }: { query: UseQueryResult<MailingForecastView> }) {
  return (
    <Stack gap="xs" className="mailingEmpty">
      <NotRefreshed query={query} />
      <Text size="sm" px="md">
        Очередь пуста: новые письма собирают и отправляют в{' '}
        <Anchor component={Link} to="/letters?stage=advertisers">
          «Письмах» рекламодателей
        </Anchor>
        .
      </Text>
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
  // Очередь этапа — обеих вкладок: прогноз считает её целиком.
  const empty = view !== undefined && view.queued_first === 0;
  return (
    <Card className="glassPanel" p="md">
      <Stack gap="lg">
        <Group px="md">
          <Title order={3}>Рассылка рекламодателям</Title>
        </Group>
        <MailingBlocked view={view} />
        <Modes query={modes} empty={empty} />
        {empty ? <EmptyQueue query={forecast} /> : <MailingQueue query={forecast} />}
        <Parts query={facets} />
        {view === undefined ? null : <MailingDaysTabs view={view} words={PLAN_WORDS} />}
      </Stack>
    </Card>
  );
}
