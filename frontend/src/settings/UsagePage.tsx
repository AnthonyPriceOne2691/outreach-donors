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
 */

import { Alert, Card, Group, Loader, SimpleGrid, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { USAGE_PROVIDERS } from '../api/labels';
import { Meter } from '../components/Meter';
import { Metric } from '../components/Metric';
import { PageHead } from '../components/PageHead';
import { SpendTable, tileOf } from '../components/SpendTable';
import { fetchUsage } from '../api/settings';
import { formatDate, formatNumber, formatUsd } from '../format';

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
            hint={`С ${formatDate(data.since)} — лимиты месячные. Остаток спрашивается у провайдера, а не считается по своей таблице: ключ Ahrefs общий с соседней системой, и её траты нам не видны.`}
          />

          {/* Три числа, и путать их нельзя: остаток у провайдера включает
              траты соседней системы на общем ключе, а с нашим капом
              сравнивают наш же расход. Первая версия экрана показывала
              «израсходовано 0» при шести тысячах потраченных юнитов. */}
          <Stack gap={6}>
            <Group justify="space-between">
              <Text size="sm">
                Мы потратили с начала месяца: <b>{formatNumber(data.ahrefs_spent_by_us)}</b> из{' '}
                {formatNumber(data.ahrefs_cap)} по нашему капу
              </Text>
              {data.ahrefs_left === null ? (
                <Text size="sm" c="dimmed">
                  остаток у провайдера неизвестен
                </Text>
              ) : (
                <Text size="sm" c="dimmed">
                  у провайдера осталось {formatNumber(data.ahrefs_left)} — с учётом чужих трат на
                  общем ключе
                </Text>
              )}
            </Group>
            <Meter
              spent={data.ahrefs_spent_by_us}
              cap={data.ahrefs_cap}
              label="Юниты Ahrefs с начала месяца"
            />
          </Stack>

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

          {/* Плитки — внутри шапки, как на «Отборе» и «Продажах»: на полотне они
              стояли отдельным рядом и в тёмной теме читались провалами (аудит
              экранов 09.10.2026). */}
          <SimpleGrid cols={{ base: 2, sm: 3, lg: 5 }} spacing="sm">
            {Object.entries(USAGE_PROVIDERS).map(([key, provider]) => {
              const tile = tileOf(
                key,
                data.units_by_provider[key] ?? 0,
                Number(data.amount_by_provider[key] ?? '0'),
              );
              return (
                <Metric key={key} title={provider.title} value={tile.value} hint={tile.hint} />
              );
            })}
          </SimpleGrid>
          <SalesHidden hidden={data.sales_hidden} />
        </Stack>
      </Card>

      {/* Поля карточки с таблицей — вместе с полем ячейки те же 32 px, что
          у панели сверху: текст соседних карточек начинается с одного места. */}
      <Card className="glass" p="md">
        {data.articles.length === 0 ? (
          <Text size="sm" c="dimmed" p="md">
            С начала месяца трат не было. Это не поломка учёта: расход пишется той же транзакцией,
            что и сам запрос к провайдеру.
          </Text>
        ) : (
          <SpendTable articles={data.articles} />
        )}
      </Card>
    </Stack>
  );
}
