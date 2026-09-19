/**
 * Прогон — единственный экран, где человек тратит деньги, и единственный,
 * где кнопка блокируется расчётом.
 *
 * **Стоимость называется до того, как её потратят.** Смета считается
 * отдельным запросом, который ничего не тратит: остаток у провайдера
 * спрашивается бесплатно, число доменов оценивается по замеренной доле
 * уникальных.
 *
 * **Числа сметы подписаны как приблизительные** — и это не скромность:
 * точное число доменов известно только после выдачи, а выдача уже трата.
 * Смета считает худший случай (все домены новые), а точная проверка идёт
 * второй раз, по настоящим доменам, до первого платного запроса.
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Textarea,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCalculator, IconPlayerPlay } from '@tabler/icons-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { countryTitle, RUN_STATUSES } from '../api/labels';
import { estimateRun, fetchCountries, listRuns, startRun } from '../api/runs';
import type { Forecast } from '../api/types';
import { useSession } from '../auth/AuthProvider';

function refusalOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Сервер отказал без объяснения';
}

function parseKeywords(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '');
}

function Number({
  title,
  value,
  hint,
}: {
  title: string;
  value: string;
  hint?: string | undefined;
}) {
  return (
    <Card className="glassQuiet" p="md">
      <Text size="xs" c="dimmed">
        {title}
      </Text>
      <Text fw={600} fz="xl">
        {value}
      </Text>
      {hint !== undefined && (
        <Text size="xs" c="dimmed">
          {hint}
        </Text>
      )}
    </Card>
  );
}

function Estimate({ forecast }: { forecast: Forecast }) {
  return (
    <Stack gap="sm">
      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
        <Number
          title="Результатов выдачи"
          value={String(forecast.expected_results)}
          hint={`${forecast.keywords} ключей × ${forecast.depth_pages} стр.`}
        />
        <Number
          title="Уникальных доменов"
          value={`≈ ${forecast.expected_domains}`}
          hint="83% схлопывается в дубли — по замеру"
        />
        <Number
          title="Юнитов Ahrefs"
          value={`до ${forecast.units_total}`}
          hint={`просев ${forecast.units_screen} · метрики ${forecast.units_metrics} · гео ${forecast.units_by_country}`}
        />
        <Number
          title="Бюджет прогона"
          value={String(forecast.budget)}
          hint={`остаток ${forecast.units_left}, кап ${forecast.units_cap}`}
        />
      </SimpleGrid>

      {forecast.affordable ? (
        <Alert color="teal" title="Помещается">
          Смета считает худший случай — что все домены новые. За те, у которых данные ещё свежие,
          второй раз не платят, поэтому по факту обычно меньше.
        </Alert>
      ) : (
        <Alert color="red" title="Не помещается в бюджет">
          Не хватает {forecast.shortfall} юнитов. Сократите список ключей или глубину — либо
          поднимите кап, если остаток у провайдера позволяет.
        </Alert>
      )}
    </Stack>
  );
}

export function RunPage() {
  const { can } = useSession();
  const [keywords, setKeywords] = useState('');
  const [country, setCountry] = useState('us');
  const [depth, setDepth] = useState(1);
  const [forecast, setForecast] = useState<Forecast | null>(null);

  const countries = useQuery({ queryKey: ['countries'], queryFn: fetchCountries });
  const runs = useQuery({ queryKey: ['runs'], queryFn: listRuns });

  const list = parseKeywords(keywords);
  const body = { keywords: list, country, depth_pages: depth };

  const estimate = useMutation({
    mutationFn: () => estimateRun(body),
    onSuccess: setForecast,
    onError: (failure) =>
      notifications.show({
        title: 'Смета не посчиталась',
        message: refusalOf(failure),
        color: 'red',
      }),
  });

  const launch = useMutation({
    mutationFn: () => startRun(body),
    onSuccess: async (queued) => {
      notifications.show({ title: 'Прогон в очереди', message: queued.note, color: 'green' });
      setForecast(null);
      await runs.refetch();
    },
    onError: (failure) =>
      notifications.show({ title: 'Прогон не запущен', message: refusalOf(failure), color: 'red' }),
  });

  // Смета устаревает, как только меняют ключи или страну: показывать
  // старые числа рядом с новым списком — это обещание цены, которой нет.
  const stale = forecast !== null && forecast.keywords !== list.length;
  const canRun = can('run');

  return (
    <Stack gap="lg" maw={1000}>
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          <Stack gap={6}>
            <Title order={3}>Прогон</Title>
            <Text size="sm" c="dimmed" maw={680}>
              Стоимость называется до того, как её потратят: сначала смета, потом кнопка. Пока смета
              не посчитана, запускать нечего — прогон без сверки с остатком это прямой путь к
              выбранному месячному лимиту за один день.
            </Text>
          </Stack>

          <Textarea
            label="Ключевые слова"
            description="По одному в строке"
            placeholder={'ремонт квартир\nдизайн интерьера'}
            autosize
            minRows={4}
            maxRows={12}
            value={keywords}
            onChange={(event) => setKeywords(event.currentTarget.value)}
          />

          <Group align="flex-end" gap="md">
            <Select
              label="Страна выдачи"
              description="Список приходит с сервера"
              w={260}
              searchable
              value={country}
              data={(countries.data ?? []).map((code) => ({
                value: code,
                label: countryTitle(code),
              }))}
              onChange={(value) => value !== null && setCountry(value)}
            />
            <NumberInput
              label="Глубина, страниц"
              description="10 результатов на странице"
              w={180}
              min={1}
              max={5}
              value={depth}
              onChange={(value) => setDepth(typeof value === 'number' ? value : 1)}
            />
            <Button
              variant="default"
              className="press"
              leftSection={<IconCalculator size={18} />}
              loading={estimate.isPending}
              disabled={list.length === 0}
              onClick={() => estimate.mutate()}
            >
              Посчитать смету
            </Button>
            <Button
              className="press"
              variant="gradient"
              gradient={{ from: 'lagoon.5', to: 'lagoon.7', deg: 135 }}
              leftSection={<IconPlayerPlay size={18} />}
              loading={launch.isPending}
              disabled={forecast === null || !forecast.affordable || stale || !canRun}
              onClick={() => launch.mutate()}
            >
              Запустить
            </Button>
          </Group>

          {stale && (
            <Alert color="yellow" title="Смета устарела">
              Список ключей изменился после расчёта. Посчитайте смету заново — иначе кнопка обещает
              цену, которой уже нет.
            </Alert>
          )}
          {!canRun && (
            <Alert color="yellow" title="Запускать прогоны не разрешено">
              Смету посмотреть можно, запуск тратит юниты и выдан отдельным правом.
            </Alert>
          )}
        </Stack>
      </Card>

      {forecast !== null && !stale && (
        <Card className="glass" p="lg">
          <Title order={5} mb="sm">
            Смета
          </Title>
          <Estimate forecast={forecast} />
        </Card>
      )}

      <Card className="glass" p="xs">
        <Table className="dataTable" verticalSpacing="sm" horizontalSpacing="md" miw={760}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Прогон</Table.Th>
              <Table.Th>Состояние</Table.Th>
              <Table.Th>Ключей</Table.Th>
              <Table.Th>Смета</Table.Th>
              <Table.Th>Факт</Table.Th>
              <Table.Th>Расхождение</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {(runs.data ?? []).map((run) => (
              <Table.Tr key={run.id}>
                <Table.Td>
                  <Text fw={500}>№{run.id}</Text>
                  <Text size="xs" c="dimmed">
                    {countryTitle(run.country)} · {new Date(run.started_at).toLocaleString('ru-RU')}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Group justify="center">
                    <Badge variant="light" color={RUN_STATUSES[run.status].color}>
                      {RUN_STATUSES[run.status].title}
                    </Badge>
                  </Group>
                </Table.Td>
                <Table.Td>{run.keywords}</Table.Td>
                <Table.Td>{run.estimated_units ?? '—'}</Table.Td>
                <Table.Td>{run.actual_units ?? '—'}</Table.Td>
                <Table.Td>
                  {/* Расхождение сметы и факта — единственная проверка сметы.
                      Без неё оценка расхода ничем не подтверждается. */}
                  {run.estimate_error === null
                    ? '—'
                    : `${run.estimate_error > 0 ? '+' : ''}${(run.estimate_error * 100).toFixed(0)}%`}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        {(runs.data ?? []).length === 0 && (
          <Text size="sm" c="dimmed" p="lg">
            Прогонов ещё не было. Первый появится здесь сразу после запуска — вместе со сметой, с
            которой его потом сравнят.
          </Text>
        )}
      </Card>
    </Stack>
  );
}
