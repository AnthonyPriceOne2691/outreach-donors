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
import { formatDate, formatNumber, formatUsd, plural } from '../format';

/** Единица счёта у каждого провайдера — своя: Ahrefs берёт юнитами,
 *  модель — токенами, отправка считает письма. «Юн.» у всех подряд
 *  называло токены модели юнитами Ahrefs. */
const UNIT_TITLES: Record<string, string> = {
  ahrefs: 'юн.',
  llm: 'ток.',
};

/** Та же единица словом — для плитки, где под числом есть место. */
const UNITS: [string, string, string] = ['юнит', 'юнита', 'юнитов'];
const UNIT_WORDS: Record<string, [string, string, string]> = {
  ahrefs: UNITS,
  llm: ['токен', 'токена', 'токенов'],
  email: ['письмо', 'письма', 'писем'],
};

/** «1 письмо, 4 письма, 5 писем» — у сокращений склонять нечего. */
function unitOf(provider: string, count: number): string {
  if (provider !== 'email') return UNIT_TITLES[provider] ?? 'юн.';
  return plural(count, 'письмо', 'письма', 'писем');
}

/** Единица словом, согласованная с числом: «6 416 юнитов», «4 письма». */
function unitWord(provider: string, count: number): string {
  const [one, few, many] = UNIT_WORDS[provider] ?? UNITS;
  return plural(count, one, few, many);
}

/** Плитка провайдера: число — крупно, единица и деньги — подписью под ним. */
function tileOf(
  provider: string,
  units: number,
  amount: number,
): { value: string; hint: string | undefined } {
  if (units > 0) {
    const unit = unitWord(provider, units);
    return {
      value: formatNumber(units),
      hint: amount > 0 ? `${unit} и ${formatUsd(amount)}` : unit,
    };
  }
  // У провайдеров разная валюта счёта: Ahrefs берёт юнитами, источник
  // выдачи — деньгами, модель — токенами. Показывать «0.00 $» там, где
  // платят не деньгами, значит уверять, что трат не было: так экран
  // и врал про выдачу до этого среза.
  if (amount > 0) return { value: formatUsd(amount), hint: undefined };
  return { value: '—', hint: 'трат не было' };
}

/** Колонки статей. Первая — остаток: в ней название статьи. Остальные — по
 *  самому длинному, замеренному шрифтом экрана 25.09.2026: значок «метрики
 *  Ahrefs» — 116 px (на телефоне он ужимался до «метрики Ahr…»), «12 345 678
 *  ток.» — 104, «1 234,56 $» — 69. Плюс 32 px полей ячейки. */
const COLUMNS: { title: string; width?: string }[] = [
  { title: 'Статья' },
  { title: 'Провайдер', width: '9.5rem' },
  { title: 'Запросов', width: '7rem' },
  { title: 'Единиц', width: '9rem' },
  { title: 'Денег', width: '7rem' },
];
const TABLE_MIN_WIDTH = 720;

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
          const tile = tileOf(
            key,
            data.units_by_provider[key] ?? 0,
            Number(data.amount_by_provider[key] ?? '0'),
          );
          return <Metric key={key} title={provider.title} value={tile.value} hint={tile.hint} />;
        })}
      </SimpleGrid>

      {/* Поля карточки с таблицей — вместе с полем ячейки те же 32 px, что
          у панели сверху: текст соседних карточек начинается с одного места. */}
      <Card className="glass" p="md">
        {data.articles.length === 0 ? (
          <Text size="sm" c="dimmed" p="md">
            С начала месяца трат не было. Это не поломка учёта: расход пишется той же транзакцией,
            что и сам запрос к провайдеру.
          </Text>
        ) : (
          <Table.ScrollContainer minWidth={TABLE_MIN_WIDTH} type="native" className="scrollSlim">
            <Table
              className="dataTable fixedTable"
              layout="fixed"
              tabularNums
              verticalSpacing="sm"
              horizontalSpacing="md"
            >
              <colgroup>
                {COLUMNS.map((column) => (
                  <col
                    key={column.title}
                    style={column.width ? { width: column.width } : undefined}
                  />
                ))}
              </colgroup>
              <Table.Thead>
                <Table.Tr>
                  {COLUMNS.map((column) => (
                    <Table.Th key={column.title}>{column.title}</Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.articles.map((article) => (
                  <Table.Tr key={`${article.provider}-${article.operation}`}>
                    <Table.Td>
                      <Text fw={500} className="cellName">
                        {operationTitle(article.operation)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Badge variant="light" color={USAGE_PROVIDERS[article.provider].color}>
                        {USAGE_PROVIDERS[article.provider].title}
                      </Badge>
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
