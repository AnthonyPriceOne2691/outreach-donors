/**
 * Расход: на что ушли деньги и сколько осталось.
 *
 * **Остаток и расход отвечают на разные вопросы, и берутся из разных
 * мест.** Сколько осталось — у провайдера: ключ Ahrefs общий с соседней
 * системой, и наша таблица не видит её трат. На что потратили мы — своя
 * таблица, и этот ответ от провайдера не зависит.
 *
 * Поэтому недоступный остаток не прячет расход: он показывается
 * отдельной строкой «спросить не удалось», а не пустым экраном.
 *
 * **Единица в плитке — словом под числом, а не хвостом за ним.** На
 * телефоне «2 180 924 ток.» не влезало в плитку и ломалось на «2 180 924»
 * и «ток.» отдельной строкой (аудит 25.09.2026). Число — крупно, «токенов»
 * — подписью, как «подходящих 840» у плиток писем.
 *
 * **Без права «Продажи» — расход без продаж** (решение Anthony 10.10.2026, П2б):
 * статьи, плитки и итог считает сервер без операций продаж, и сходятся они между
 * собой. Что это не весь счёт, экран говорит строкой под плитками.
 *
 * **Этап — у каждой статьи, разбивка — отдельной панелью** (`UsageStages.tsx`):
 * DR кандидатов Этапа 2 и метрики прогона — одна операция, но две статьи; платный
 * обход Этапа 2 — строкой против своего потолка.
 *
 * **Кап Ahrefs — от ключа, сузить его можно здесь** (`AhrefsCap.tsx`, решения владельца
 * 16.10.2026): размер ключа и цикл называет Ahrefs, сколько потрачено и осталось, кто и когда
 * менял — и «Изменить кап» у кого есть право.
 *
 * **Два периода — подписаны оба** (16.10.2026): кап — в цикле ключа, со своего обнуления,
 * плитки, этапы и статьи — с первого числа. Без подписи два числа Ahrefs на одном экране
 * читались бы одним.
 *
 * **Потолки модели — здесь же** (`LlmCaps.tsx`, слово Anthony 29.10.2026): общий на весь сервис
 * за сутки, лимиты разделов и свободный остаток, — и «Изменить потолки» у администратора.
 *
 * **Две вкладки вместо простыни** (слово Anthony 02.11.2026: «всё простынёй и непонятно»).
 * «Лимиты» отвечает «сколько осталось и что поменять» — карточками одного устройства
 * (`LimitCard.tsx`), «Статьи расхода» — «на что ушло с первого числа». Вместе экран занимал три с
 * лишним окна телефона, а вопросы у них разные. Вкладка живёт в адресе: «Расход» открывается на
 * лимитах, и ссылки сметы «поднимите кап на экране «Расход»» ведут сразу к ним. Вкладки заданы
 * списком (`TABS`): новая — ещё одна строка и ещё одна ветка.
 */

import { Alert, Card, Loader, SegmentedControl, SimpleGrid, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';

import { refusalOf } from '../api/client';
import { Metric } from '../components/Metric';
import { PageHead } from '../components/PageHead';
import { SpendTable, tileOf } from '../components/SpendTable';
import { fetchUsage } from '../api/settings';
import type { UsageView } from '../api/settings';
import { formatDay } from '../format';
import { AhrefsCap } from './AhrefsCap';
import { HunterLimit } from './HunterLimit';
import { LimitGrid } from './LimitCard';
import { LlmCaps } from './LlmCaps';
import { CrawlCap, SerpFunds, StageBreakdown } from './UsageStages';
import { shownWithoutSpend, SPEND_PROVIDERS } from './usageLabels';

type UsageTab = 'limits' | 'articles';

const TABS: { value: UsageTab; label: string }[] = [
  { value: 'limits', label: 'Лимиты' },
  { value: 'articles', label: 'Статьи расхода' },
];

/** Вкладка — из адреса; незнакомая или пустая — лимиты. */
function useUsageTab(): [UsageTab, (tab: UsageTab) => void] {
  const [params, setParams] = useSearchParams();
  const asked = params.get('view');
  const tab = TABS.find((known) => known.value === asked)?.value ?? 'limits';
  const go = useCallback(
    (next: UsageTab) => {
      if (next === tab) return;
      setParams(next === 'limits' ? new URLSearchParams() : new URLSearchParams({ view: next }));
    },
    [tab, setParams],
  );
  return [tab, go];
}

/** Без права «Продажи» сервер считает расход без продаж (П2б): числа сходятся между собой,
 *  но это не весь счёт — и экран так и говорит. */
function SalesHidden({ hidden }: { hidden: boolean }) {
  if (!hidden) return null;
  return (
    <Text size="sm" c="dimmed">
      Расход продаж не показан — его видно с правом «Продажи». Плитки и статьи — без него: это не
      весь счёт.
    </Text>
  );
}

/** Сколько осталось и что поменять: карточка на счёт; нет права — строка, у кого оно. */
function Limits({ data }: { data: UsageView }) {
  return (
    <>
      {data.ahrefs_left === null && (
        <Alert color="yellow" title="Остаток у Ahrefs спросить не удалось">
          {data.ahrefs_left_error ?? 'Провайдер не ответил.'} Прогон при недоступном остатке не
          запускается: тратить вслепую нельзя.
        </Alert>
      )}
      <LimitGrid>
        {/* Ключ и кап — менять тому, у кого право «править пороги» (`AhrefsCap.tsx`). */}
        <AhrefsCap ahrefsKey={data.ahrefs_key} />
        {/* Вся учётка и наш потолок проверок — менять с правом «менять потолки расхода». */}
        {data.hunter !== null && <HunterLimit hunter={data.hunter} />}
        {/* Общий потолок и лимиты разделов — то же право (`LlmCaps.tsx`). */}
        {data.llm_caps !== null && <LlmCaps caps={data.llm_caps} />}
        {/* Выдача — счёт в деньгах; ключ там свой, не общий: чужих трат в остатке нет. */}
        <SerpFunds
          left={data.serp_left_usd}
          spent={data.serp_spent_by_us}
          error={data.serp_left_error}
        />
        {/* Платный обход Этапа 2 — в деньгах и со своим потолком, отдельно от юнитов Ahrefs. */}
        <CrawlCap crawl={data.crawl} />
      </LimitGrid>
    </>
  );
}

/** Плитки — внутри шапки, как на «Отборе» и «Продажах»: на полотне они стояли отдельным рядом и
 *  в тёмной теме читались провалами (аудит экранов 09.10.2026). Период — словами над ними: у капа
 *  он другой. */
function SpendTiles({ data }: { data: UsageView }) {
  return (
    <>
      <Text size="sm" c="dimmed">
        Расход с {formatDay(data.since)} — календарный месяц: плитки, этапы и статьи
      </Text>
      <SimpleGrid cols={{ base: 2, sm: 3, lg: 5 }} spacing="sm">
        {Object.entries(SPEND_PROVIDERS).map(([key, provider]) => {
          const units = data.units_by_provider[key] ?? 0;
          const amount = Number(data.amount_by_provider[key] ?? '0');
          if (!shownWithoutSpend(key) && units === 0 && amount === 0) return null;
          const tile = tileOf(key, units, amount);
          return <Metric key={key} title={provider.title} value={tile.value} hint={tile.hint} />;
        })}
      </SimpleGrid>
      <SalesHidden hidden={data.sales_hidden} />
    </>
  );
}

/** На что ушло: этапы и статьи — своими карточками под шапкой. Поля карточки с таблицей — вместе
 *  с полем ячейки те же 32 px, что у панели сверху: текст соседних карточек начинается с одного
 *  места. */
function SpendArticles({ data }: { data: UsageView }) {
  return (
    <>
      <StageBreakdown stages={data.stages} />
      <Card className="glass" p="md">
        {data.articles.length === 0 ? (
          <Text size="sm" c="dimmed" p="md">
            С начала месяца трат не было. Это не поломка учёта: расход пишется той же транзакцией,
            что и сам запрос к провайдеру.
          </Text>
        ) : (
          <SpendTable articles={data.articles} stages />
        )}
      </Card>
    </>
  );
}

export function UsagePage() {
  const { data, isLoading, error } = useQuery({ queryKey: ['usage'], queryFn: fetchUsage });
  const [tab, setTab] = useUsageTab();

  if (isLoading) return <Loader aria-label="Загружаем расход" m="md" />;
  if (error) {
    return (
      <Alert color="red" title="Расход не загрузился" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }
  if (data === undefined) return null;

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          <PageHead
            title="Расход"
            hint={`На вкладке лимитов кап Ahrefs — в цикле ключа, со своего обнуления, а статьи расхода — с ${formatDay(data.since)}, календарный месяц. Остаток спрашивается у провайдера, а не считается по своей таблице: ключ Ahrefs и учётка Hunter общие с соседней системой, и её траты нам не видны.`}
          />
          <SegmentedControl
            aria-label="Что смотреть на экране «Расход»"
            value={tab}
            onChange={(picked) =>
              setTab(TABS.find((known) => known.value === picked)?.value ?? tab)
            }
            data={TABS}
            style={{ alignSelf: 'flex-start' }}
          />
          {tab === 'limits' ? <Limits data={data} /> : <SpendTiles data={data} />}
        </Stack>
      </Card>
      {tab === 'articles' && <SpendArticles data={data} />}
    </Stack>
  );
}
