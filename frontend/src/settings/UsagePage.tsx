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
  Progress,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { operationTitle, USAGE_PROVIDERS } from '../api/labels';
import { Metric } from '../components/Metric';
import { fetchUsage } from '../api/settings';

function refusalOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Сервер отказал без объяснения';
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
    <Stack gap="lg" maw={1000}>
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          <Stack gap={6}>
            <Title order={3}>Расход</Title>
            <Text size="sm" c="dimmed" maw={680}>
              С {new Date(data.since).toLocaleDateString('ru-RU')} — лимиты месячные. Остаток
              спрашивается у провайдера, а не считается по своей таблице: ключ Ahrefs общий с
              соседней системой, и её траты нам не видны.
            </Text>
          </Stack>

          {/* Три числа, и путать их нельзя: остаток у провайдера включает
              траты соседней системы на общем ключе, а с нашим капом
              сравнивают наш же расход. Первая версия экрана показывала
              «израсходовано 0» при шести тысячах потраченных юнитов. */}
          <Stack gap={6}>
            <Group justify="space-between">
              <Text size="sm">
                Мы потратили с начала месяца: <b>{data.ahrefs_spent_by_us}</b> из {data.ahrefs_cap}{' '}
                по нашему капу
              </Text>
              {data.ahrefs_left === null ? (
                <Text size="sm" c="dimmed">
                  остаток у провайдера неизвестен
                </Text>
              ) : (
                <Text size="sm" c="dimmed">
                  у провайдера осталось {data.ahrefs_left} — с учётом чужих трат на общем ключе
                </Text>
              )}
            </Group>
            <Progress
              value={
                data.ahrefs_cap === 0
                  ? 0
                  : Math.min(100, (data.ahrefs_spent_by_us / data.ahrefs_cap) * 100)
              }
              color="lagoon"
              radius="xl"
              size="sm"
            />
          </Stack>

          {data.ahrefs_left === null && (
            <Alert color="yellow" title="Остаток у Ahrefs спросить не удалось">
              {data.ahrefs_left_error ?? 'Провайдер не ответил.'} Прогон при недоступном остатке не
              запускается: тратить вслепую нельзя.
            </Alert>
          )}
        </Stack>
      </Card>

      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
        {Object.entries(USAGE_PROVIDERS).map(([key, provider]) => {
          const units = data.units_by_provider[key] ?? 0;
          const amount = Number(data.amount_by_provider[key] ?? '0');
          const spent = units > 0 || amount > 0;
          return (
            <Metric
              key={key}
              title={provider.title}
              value={units > 0 ? `${units} юн.` : `${amount.toFixed(2)} $`}
              hint={
                spent
                  ? units > 0 && amount > 0
                    ? `и ${amount.toFixed(2)} $`
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
          <Table className="dataTable" verticalSpacing="sm" horizontalSpacing="md">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Статья</Table.Th>
                <Table.Th>Провайдер</Table.Th>
                <Table.Th>Запросов</Table.Th>
                <Table.Th>Юнитов</Table.Th>
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
                  <Table.Td>{article.calls}</Table.Td>
                  <Table.Td>{article.units > 0 ? article.units : '—'}</Table.Td>
                  <Table.Td>
                    {Number(article.amount_usd) > 0
                      ? `${Number(article.amount_usd).toFixed(2)} $`
                      : '—'}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Card>
    </Stack>
  );
}
