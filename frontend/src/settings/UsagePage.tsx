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
 */

import {
  Alert,
  Badge,
  Card,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { operationTitle, USAGE_PROVIDERS } from '../api/labels';
import { Meter } from '../components/Meter';
import { Metric } from '../components/Metric';
import { fetchUsage } from '../api/settings';
import { formatDate, formatNumber, formatUsd } from '../format';

/** Единица счёта у каждого провайдера — своя: Ahrefs берёт юнитами,
 *  модель — токенами, отправка считает письма. «Юн.» у всех подряд
 *  называло токены модели юнитами Ahrefs. */
const UNIT_TITLES: Record<string, string> = {
  ahrefs: 'юн.',
  llm: 'ток.',
};

/** «1 письмо, 4 письма, 5 писем» — у сокращений склонять нечего. */
function unitOf(provider: string, count: number): string {
  if (provider !== 'email') return UNIT_TITLES[provider] ?? 'юн.';
  const tens = count % 100;
  const ones = count % 10;
  if (tens >= 11 && tens <= 14) return 'писем';
  if (ones === 1) return 'письмо';
  if (ones >= 2 && ones <= 4) return 'письма';
  return 'писем';
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
          <Stack gap={6}>
            <Title order={3}>Расход</Title>
            <Text size="sm" c="dimmed" maw={680}>
              С {formatDate(data.since)} — лимиты месячные. Остаток спрашивается у провайдера, а не
              считается по своей таблице: ключ Ahrefs общий с соседней системой, и её траты нам не
              видны.
            </Text>
          </Stack>

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
        </Stack>
      </Card>

      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
        {Object.entries(USAGE_PROVIDERS).map(([key, provider]) => {
          const units = data.units_by_provider[key] ?? 0;
          const amount = Number(data.amount_by_provider[key] ?? '0');
          const spent = units > 0 || amount > 0;
          // У провайдеров разная валюта счёта: Ahrefs берёт юнитами,
          // источник выдачи — деньгами, модель — токенами. Показывать
          // «0.00 $» там, где платят не деньгами, значит уверять, что
          // трат не было: так экран и врал про выдачу до этого среза.
          const unit = unitOf(key, units);
          const value =
            units > 0 ? `${formatNumber(units)} ${unit}` : amount > 0 ? formatUsd(amount) : '—';
          return (
            <Metric
              key={key}
              title={provider.title}
              value={value}
              hint={
                spent
                  ? units > 0 && amount > 0
                    ? `и ${formatUsd(amount)}`
                    : undefined
                  : 'трат не было'
              }
            />
          );
        })}
      </SimpleGrid>

      <Card className="glass" p="xs">
        {data.articles.length === 0 ? (
          <Text size="sm" c="dimmed" p="lg">
            С начала месяца трат не было. Это не поломка учёта: расход пишется той же транзакцией,
            что и сам запрос к провайдеру.
          </Text>
        ) : (
          <Table.ScrollContainer minWidth={620}>
            <Table className="dataTable" verticalSpacing="sm" horizontalSpacing="md">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Статья</Table.Th>
                  <Table.Th>Провайдер</Table.Th>
                  <Table.Th>Запросов</Table.Th>
                  <Table.Th>Единиц</Table.Th>
                  <Table.Th>Денег</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.articles.map((article) => (
                  <Table.Tr key={`${article.provider}-${article.operation}`}>
                    <Table.Td>
                      <Text fw={500}>{operationTitle(article.operation)}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Group justify="center">
                        <Badge variant="light" color={USAGE_PROVIDERS[article.provider].color}>
                          {USAGE_PROVIDERS[article.provider].title}
                        </Badge>
                      </Group>
                    </Table.Td>
                    <Table.Td>{formatNumber(article.calls)}</Table.Td>
                    <Table.Td>
                      {article.units > 0
                        ? `${formatNumber(article.units)} ${unitOf(article.provider, article.units)}`
                        : '—'}
                    </Table.Td>
                    <Table.Td>
                      {Number(article.amount_usd) > 0 ? formatUsd(article.amount_usd) : '—'}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>
    </Stack>
  );
}
