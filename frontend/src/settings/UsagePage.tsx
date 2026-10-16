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
 */

import { Alert, Card, Group, Loader, SimpleGrid, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { Metric } from '../components/Metric';
import { PageHead } from '../components/PageHead';
import { SpendTable, tileOf } from '../components/SpendTable';
import { fetchUsage } from '../api/settings';
import { formatDay, formatUsd } from '../format';
import { AhrefsCap } from './AhrefsCap';
import { CrawlCap, StageBreakdown } from './UsageStages';
import { shownWithoutSpend, SPEND_PROVIDERS } from './usageLabels';

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

export function UsagePage() {
  const { data, isLoading, error } = useQuery({ queryKey: ['usage'], queryFn: fetchUsage });

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
            hint={`Кап Ahrefs — в цикле ключа, со своего обнуления; плитки, этапы и статьи — с ${formatDay(data.since)}, календарный месяц. Остаток спрашивается у провайдера, а не считается по своей таблице: ключ Ahrefs общий с соседней системой, и её траты нам не видны.`}
          />

          {/* Ключ и кап — размер ключа, цикл, сколько потрачено и осталось, кто и когда менял;
              менять — тому, у кого право «править пороги» (`AhrefsCap.tsx`). */}
          <AhrefsCap ahrefsKey={data.ahrefs_key} />

          {data.ahrefs_left === null && (
            <Alert color="yellow" title="Остаток у Ahrefs спросить не удалось">
              {data.ahrefs_left_error ?? 'Провайдер не ответил.'} Прогон при недоступном остатке не
              запускается: тратить вслепую нельзя.
            </Alert>
          )}

          {/* Выдача — второй счёт, и он в деньгах. Ключ там свой, не общий,
              поэтому чужих трат в остатке нет и вычитать нечего. */}
          <Group justify="space-between">
            <Text size="sm">
              Выдача с начала месяца: <b>{formatUsd(data.serp_spent_by_us)}</b>
            </Text>
            <Text size="sm" c="dimmed">
              {data.serp_left_usd === null
                ? (data.serp_left_error ?? 'остаток у источника выдачи неизвестен')
                : `на счету источника выдачи ${formatUsd(data.serp_left_usd)}`}
            </Text>
          </Group>

          {/* Платный обход Этапа 2 — третий счёт, в деньгах и со своим потолком:
              отдельно от юнитов Ahrefs, как того требует ТЗ. */}
          <CrawlCap crawl={data.crawl} />

          {/* Плитки — внутри шапки, как на «Отборе» и «Продажах»: на полотне они
              стояли отдельным рядом и в тёмной теме читались провалами (аудит
              экранов 09.10.2026). Период — словами над ними: у капа выше он другой. */}
          <Text size="sm" c="dimmed">
            Расход с {formatDay(data.since)} — календарный месяц: плитки, этапы и статьи
          </Text>
          <SimpleGrid cols={{ base: 2, sm: 3, lg: 5 }} spacing="sm">
            {Object.entries(SPEND_PROVIDERS).map(([key, provider]) => {
              const units = data.units_by_provider[key] ?? 0;
              const amount = Number(data.amount_by_provider[key] ?? '0');
              if (!shownWithoutSpend(key) && units === 0 && amount === 0) return null;
              const tile = tileOf(key, units, amount);
              return (
                <Metric key={key} title={provider.title} value={tile.value} hint={tile.hint} />
              );
            })}
          </SimpleGrid>
          <SalesHidden hidden={data.sales_hidden} />
        </Stack>
      </Card>

      <StageBreakdown stages={data.stages} />

      {/* Поля карточки с таблицей — вместе с полем ячейки те же 32 px, что
          у панели сверху: текст соседних карточек начинается с одного места. */}
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
    </Stack>
  );
}
