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
  Button,
  Card,
  Group,
  NumberInput,
  Select,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Text,
  Textarea,
  TagsInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCalculator, IconPlayerPlay, IconSparkles } from '@tabler/icons-react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { countryTitle } from '../api/labels';
import { Metric } from '../components/Metric';
import { buildPool, fetchMarketLanguages, fetchPresets } from '../api/keywords';
import { ProvenKeywords } from './ProvenKeywords';
import { ACTIVE, pagesOf, RunHistory, useHistoryPage } from './RunHistory';
import { estimateRun, fetchCountries, listRuns, startRun } from '../api/runs';
import type { Forecast } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { formatNumber, formatUsd } from '../format';

function refusalOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Сервер отказал без объяснения';
}

function parseKeywords(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '');
}

function Estimate({ forecast }: { forecast: Forecast }) {
  return (
    <Stack gap="sm">
      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
        <Metric
          title="Результатов выдачи"
          value={formatNumber(forecast.expected_results)}
          hint={`${forecast.keywords} ключей × ${forecast.depth_pages} стр.`}
        />
        <Metric
          title="Уникальных доменов"
          value={`≈ ${formatNumber(forecast.expected_domains)}`}
          hint="83% схлопывается в дубли — по замеру"
        />
        <Metric
          title="Юнитов Ahrefs"
          value={`до ${formatNumber(forecast.units_total)}`}
          hint={`просев ${formatNumber(forecast.units_screen)} · метрики ${formatNumber(forecast.units_metrics)} · гео ${formatNumber(forecast.units_by_country)}`}
        />
        <Metric
          title="Бюджет прогона"
          value={formatNumber(forecast.budget)}
          hint={
            forecast.run_ceiling === null
              ? `у провайдера ${formatNumber(forecast.units_left)}, по капу ${formatNumber(forecast.cap_left)} из ${formatNumber(forecast.units_cap)}`
              : `ваш потолок ${formatNumber(forecast.run_ceiling)}; по капу ${formatNumber(forecast.cap_left)} из ${formatNumber(forecast.units_cap)}`
          }
        />
      </SimpleGrid>

      {/* Выдача платится деньгами, а не юнитами, и кнопку не блокирует:
          без неё прогона нет вовсе. Но названа она должна быть — до этой
          строки расход на выдачу не показывался нигде. */}
      <Text size="sm" c="dimmed">
        Выдача обойдётся примерно в <b>{formatUsd(forecast.serp_cost_usd)}</b> — это другой счёт, не
        юниты Ahrefs. Потрачено нами юнитов с начала месяца:{' '}
        <b>{formatNumber(forecast.units_spent_this_month)}</b>.
      </Text>

      {forecast.affordable ? (
        <Alert color="green" title="Помещается">
          Смета считает худший случай — что все домены новые. За те, у которых данные ещё свежие,
          второй раз не платят, поэтому по факту обычно меньше.
        </Alert>
      ) : (
        <Alert color="red" title="Не помещается в бюджет">
          Не хватает {formatNumber(forecast.shortfall)} юнитов. Сократите список ключей или глубину
          — либо поднимите кап, если остаток у провайдера позволяет.
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
  const [cap, setCap] = useState<number | ''>('');
  const [forecast, setForecast] = useState<Forecast | null>(null);
  // Ключи по требованиям приносит оператор, поэтому «свои» — умолчание,
  // а сборка моделью это второй режим того же поля, а не замена ему.
  const [source, setSource] = useState<'manual' | 'model'>('manual');
  const [preset, setPreset] = useState<string | null>(null);
  const [topics, setTopics] = useState<string[]>([]);
  const [poolCap, setPoolCap] = useState<number | ''>(30);

  const countries = useQuery({ queryKey: ['countries'], queryFn: fetchCountries });
  // Пресеты спрашиваются у сервера по той же причине, что и страны:
  // второй список на фронте разъехался бы с первым добавленным набором,
  // и разошёлся бы молча.
  const presets = useQuery({
    queryKey: ['presets'],
    queryFn: fetchPresets,
    enabled: source === 'model',
  });
  // Языки выводятся из страны, а не выбираются: оператору нечем ошибиться,
  // а незнакомая страна отказывает вслух вместо тихого английского.
  const marketLanguages = useQuery({
    queryKey: ['market-languages', country],
    queryFn: () => fetchMarketLanguages(country),
    enabled: source === 'model',
  });
  const queryClient = useQueryClient();
  const [page, goToPage] = useHistoryPage();
  const runs = useQuery({
    // Ключ со словом `history`: выбор прогонов на экране писем — свой запрос
    // (`['runs', 'with-accepted']`), и страница истории его не затирает.
    // Решение на рассмотрении сбрасывает `['runs']` — то есть оба.
    queryKey: ['runs', 'history', page],
    queryFn: () => listRuns(page),
    // Пока следующая страница едет, видна прежняя: пустая таблица на долю
    // секунды читалась бы как «прогонов нет».
    placeholderData: keepPreviousData,
    // Пока есть незакрытый прогон, список обновляется сам. Без этого
    // экран молчит от нажатия до конца работы, и единственный способ
    // узнать, идёт ли она, — перезагрузить страницу.
    refetchInterval: (query) =>
      (query.state.data?.runs ?? []).some((run) => ACTIVE.has(run.status)) ? 5000 : false,
  });
  const view = runs.data ?? null;
  const rows = view?.runs ?? [];
  const waiting = rows.some((run) => run.status === 'queued');
  const pages = pagesOf(view);
  const settled = view !== null && !runs.isPlaceholderData;

  // Страница за концом — ссылка, открытая после того, как прогонов стало
  // меньше, или номер, набранный руками. Сервер отдаёт её пустой и говорит,
  // сколько всего; экран уходит на последнюю, заменяя адрес, а не добавляя.
  useEffect(() => {
    if (settled && page > pages) goToPage(pages, true);
  }, [settled, page, pages, goToPage]);

  const list = parseKeywords(keywords);
  const body = {
    keywords: list,
    country,
    depth_pages: depth,
    ...(typeof cap === 'number' ? { cap } : {}),
  };

  const pool = useMutation({
    mutationFn: () =>
      buildPool({
        preset: preset ?? 'wide',
        country,
        topics,
        cap: typeof poolCap === 'number' ? poolCap : 30,
      }),
    onSuccess: (built) => {
      // Фразы падают в то же поле, а не уходят в прогон: человек видит
      // их и правит до сметы. Смета и кап остаются последним рубежом.
      setKeywords(built.keywords.join('\n'));
      setForecast(null);
      notifications.show({
        title: `Собрано ${built.keywords.length} ключей`,
        message:
          built.refusals.length > 0
            ? `Модель ${built.model} отказала ${built.refusals.length} раз(а) — пул неполный: ${built.refusals[0]}`
            : `Модель ${built.model}, ${built.tokens} токенов, языки: ${built.languages.join(', ')}. Проверьте список перед сметой.`,
        color: built.refusals.length > 0 ? 'yellow' : 'green',
      });
    },
    onError: (failure) =>
      notifications.show({
        title: 'Пул не собрался',
        message: refusalOf(failure),
        color: 'red',
      }),
  });

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
      // Новый прогон встаёт первым на первой странице — туда и ведём, иначе
      // со второй страницы запуск выглядел бы как «ничего не случилось».
      // Сбрасывается весь `['runs']`: сдвинулись все страницы, а не одна.
      goToPage(1);
      await queryClient.invalidateQueries({ queryKey: ['runs'] });
    },
    onError: (failure) =>
      notifications.show({ title: 'Прогон не запущен', message: refusalOf(failure), color: 'red' }),
  });

  // Смета устаревает, как только меняют ключи или страну: показывать
  // старые числа рядом с новым списком — это обещание цены, которой нет.
  const stale = forecast !== null && forecast.keywords !== list.length;
  const canRun = can('run');

  return (
    <Stack gap="lg">
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

          <Stack gap="xs">
            <SegmentedControl
              value={source}
              onChange={(value) => setSource(value as 'manual' | 'model')}
              disabled={!canRun}
              data={[
                { label: 'Свои ключи', value: 'manual' },
                { label: 'Собрать моделью', value: 'model' },
              ]}
            />

            {source === 'model' ? (
              // Вложенный блок берёт `glassSolid`, а не `glassPanel`:
              // второй слой того же рецепта в тёмной теме выходит светлой
              // плитой, и весь приглушённый текст на ней выцветает —
              // намерено 2.46 : 1 при норме 4.5.
              <Card className="glassSolid" p="md" withBorder>
                <Stack gap="sm">
                  <Text size="sm" c="dimmed">
                    Сборка ничего платного не тратит — только модель. Фразы попадут в поле ниже, и
                    до сметы их можно править: смета и кап остаются последним рубежом перед тратой.
                  </Text>
                  {/* Языки называются до сборки: на двух языках пул стоит вдвое
                      дороже, и узнавать об этом по счёту неправильно. */}
                  <Text size="sm" c="dimmed">
                    {marketLanguages.isError
                      ? 'Язык этого рынка не выводится — сборка откажет и скажет почему.'
                      : `Языки рынка: ${(marketLanguages.data ?? []).join(', ') || '…'}`}
                  </Text>
                  <Group align="flex-end" gap="md" wrap="wrap">
                    <Select
                      label="Набор углов"
                      description="Свои углы не пишут — только обкатанные наборы"
                      data={presets.data ?? []}
                      value={preset}
                      onChange={setPreset}
                      placeholder={presets.isPending ? 'загружаются…' : 'wide'}
                      w={190}
                    />
                    <TagsInput
                      label="Про что"
                      description="Несколько тем дают больше доменов"
                      placeholder={topics.length === 0 ? 'ставки на спорт' : ''}
                      value={topics}
                      onChange={setTopics}
                      clearable
                      w={260}
                    />
                    <NumberInput
                      label="Сколько ключей"
                      min={1}
                      max={100}
                      value={poolCap}
                      onChange={(value) => setPoolCap(typeof value === 'number' ? value : '')}
                      w={150}
                    />
                    <Button
                      variant="light"
                      leftSection={<IconSparkles size={16} />}
                      onClick={() => pool.mutate()}
                      loading={pool.isPending}
                      disabled={!canRun}
                    >
                      Собрать
                    </Button>
                  </Group>
                </Stack>
              </Card>
            ) : null}

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

            {/* Обратная связь прошлых прогонов: ключи, которые уже давали
                принятых доноров в этой стране. */}
            {canRun ? (
              <ProvenKeywords
                country={country}
                current={list}
                onAdd={(extra) =>
                  setKeywords((was) => [was.trim(), ...extra].filter(Boolean).join('\n'))
                }
              />
            ) : null}
          </Stack>

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
            {/* Своя планка на прогон: попробовать нишу дёшево, не сокращая
                список ключей. Больше остатка по капу её всё равно не
                поднять — сервер возьмёт меньшее из двух. */}
            <NumberInput
              label="Потолок юнитов"
              description="Пусто — весь остаток по капу"
              w={200}
              min={1}
              step={1000}
              value={cap}
              onChange={(value) => setCap(typeof value === 'number' ? value : '')}
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

      {waiting && view?.workers === 0 && (
        <Alert color="yellow" title="Задачу некому взять">
          Прогон стоит в очереди, но ни один воркер её не слушает. Пока воркера нет, задача не
          выполнится — сервер при этом отвечает «поставлено», и со стороны это выглядит работающим
          сервисом. Поднимите воркер: <code>python -m backend.workers.reaper</code>
          рядом с <code>python -m backend.workers.main</code>.
        </Alert>
      )}

      {forecast !== null && !stale && (
        <Card className="glass" p="lg">
          <Title order={5} mb="sm">
            Смета
          </Title>
          <Estimate forecast={forecast} />
        </Card>
      )}

      <RunHistory view={view} page={page} onPage={goToPage} />
    </Stack>
  );
}
