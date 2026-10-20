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
 *
 * **Поля — компактным ровным рядом** (замечание 25.09.2026: «страна и
 * глубина неоправданно широкие»). Ширина поля — по самому длинному, что
 * в нём бывает («Саудовская Аравия», «100 результатов»), подписи
 * и пояснения на одной высоте (`fieldRow`), на телефоне поле — во всю
 * ширину, по одному в строке (`runFields`). Так же и в режиме сборки
 * моделью: там было три высоты подписи.
 *
 * **Режим сборки раскрывается в два такта** (`Unfold`): сначала место,
 * потом содержимое. Само содержимое — свой компонент (`ModelPool`): сборка
 * по теме и по примерам сайтов.
 *
 * **Третий вход — «Конкуренты»** (`CompetitorRun`): вместо ключей — домены конкурентов,
 * вместо выдачи — списки сайтов, которые на них ссылаются. Свои поля, смета и запуск —
 * в его файле; здесь только переключатель и место на экране.
 */

import {
  Alert,
  Button,
  Card,
  Group,
  Select,
  SegmentedControl,
  Stack,
  Text,
  Textarea,
  Title,
  useMatches,
} from '@mantine/core';
import { IconCalculator, IconPlayerPlay } from '@tabler/icons-react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { refusalOf } from '../api/client';
import { countryTitle } from '../api/labels';
import { NumberField } from '../components/NumberField';
import { numberRefusal, validNumber } from '../components/numberText';
import type { NumberRule } from '../components/numberText';
import { PageHead } from '../components/PageHead';
import { FIELD_GAP } from '../components/formRhythm';
import { usePageParam } from '../components/PageSwitch';
import { ProvenKeywords } from './ProvenKeywords';
import { ACTIVE, pagesOf, RunHistory } from './RunHistory';
import { estimateRun, fetchCountries, listRuns, startRun } from '../api/runs';
import type { Forecast, RunRequest } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { DEFAULT_DEPTH, DEPTHS, depthTitle, pagesOf as depthPages } from './depth';
import type { Depth } from './depth';
import { isCountry, isDepth, useLastChoice } from './lastChoice';
import { DEFAULT_SERP_MODE, SERP_MODES, SERP_MODE_TITLES, isSerpMode } from './serpMode';
import type { OfferedSerpMode } from './serpMode';
import { Estimate } from './Estimate';
import { ModelPool, useModelPool } from './ModelPool';
import { Unfold } from './Unfold';
import { CompetitorEstimate, CompetitorFields, useCompetitorRun } from './CompetitorRun';
import { notify, refuse, settle } from '../notices';

/** Потолок юнитов — целый. Поле держит набранное:
 *  «1.5» не становится ни пустым, ни 15, а получает отказ (проверка QA 10.10.2026).
 *  Верх потолка — тот же, что у сервера (`api/runs/schemas.py`, `MAX_RUN_CEILING`):
 *  без него двадцать девяток уходили в смету и возвращались «1e20» (проверка прода). */
const CAP: NumberRule = { decimals: 0, min: 1, max: 1_000_000 };

/** Ширины полей ряда — по самому длинному значению шрифтом экрана, а не на
 *  глаз: «Саудовская Аравия» в поле страны, «100 результатов» в поле
 *  глубины. На телефоне (уже `xs`) поле — во всю ширину: разной длины поля
 *  одно под другим читались бы рваным краем. Ряд сборки моделью — `ModelPool`. */
const WIDTH = {
  country: 200,
  depth: 176,
  serpMode: 220,
  cap: 190,
} as const;

function parseKeywords(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '');
}

export function RunPage() {
  const { can } = useSession();
  const [keywords, setKeywords] = useState('');
  const [country, setCountry] = useLastChoice('country', 'us', isCountry);
  const [depth, setDepth] = useLastChoice<Depth>('depth', DEFAULT_DEPTH, isDepth);
  const [serpMode, setSerpMode] = useLastChoice<OfferedSerpMode>(
    'serpMode',
    DEFAULT_SERP_MODE,
    isSerpMode,
  );
  const [cap, setCap] = useState('');
  const [forecast, setForecast] = useState<Forecast | null>(null);
  // О чём спрашивали смету: ключи, страна, глубина и потолок одной строкой.
  const [askedFor, setAskedFor] = useState<string | null>(null);
  // Ключи по требованиям приносит оператор, поэтому «свои» — умолчание,
  // а сборка моделью это второй режим того же поля, а не замена ему.
  const [source, setSource] = useState<'manual' | 'model' | 'competitors'>('manual');
  // Собранные моделью фразы падают в то же поле, а не уходят в прогон: человек
  // видит их и правит до сметы. Смета и кап остаются последним рубежом.
  const modelPool = useModelPool({
    country,
    open: source === 'model',
    onKeywords: (built) => {
      setKeywords(built.join('\n'));
      setForecast(null);
    },
  });

  const countries = useQuery({ queryKey: ['countries'], queryFn: fetchCountries });
  const queryClient = useQueryClient();
  const [page, goToPage] = usePageParam();
  // Новый прогон встаёт первым на первой странице — туда и ведём, как после запуска ключей.
  const rivals = useCompetitorRun(country, () => goToPage(1));
  // Три входа на телефоне в строку не встают — подписи шире 390 px окна, и переключатель
  // вылезал за панель, сдвигая её содержимое. Уже `xs` — столбиком. Ширина — с первого
  // кадра, а не из эффекта: на столе переключатель иначе прыгал бы из столбика в строку.
  const segments = useMatches<'horizontal' | 'vertical'>(
    { base: 'vertical', xs: 'horizontal' },
    { getInitialValueInEffect: false },
  );
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
      (query.state.data?.queued ?? 0) > 0 ||
      (query.state.data?.runs ?? []).some((run) => ACTIVE.has(run.status))
        ? 5000
        : false,
  });
  const view = runs.data ?? null;
  const rows = view?.runs ?? [];
  // Очередь — по всей истории, а не по этой странице: прогон, ждущий на
  // первой странице, не виден тому, кто смотрит вторую, а «некому взять»
  // касается и его.
  const waiting = (view?.queued ?? 0) > 0 || rows.some((run) => run.status === 'queued');
  const pages = pagesOf(view);
  const settled = view !== null && !runs.isPlaceholderData;

  // Страница за концом — ссылка, открытая после того, как прогонов стало
  // меньше, или номер, набранный руками. Сервер отдаёт её пустой и говорит,
  // сколько всего; экран уходит на последнюю, заменяя адрес, а не добавляя.
  useEffect(() => {
    if (settled && page > pages) goToPage(pages, true);
  }, [settled, page, pages, goToPage]);

  const list = parseKeywords(keywords);
  const capRefusal = numberRefusal(cap, CAP);
  const capValue = validNumber(cap, CAP);
  const body: RunRequest = {
    keywords: list,
    country,
    depth_pages: depthPages(depth),
    serp_mode: serpMode,
    ...(capValue !== null ? { cap: capValue } : {}),
  };

  // Удача действия снимает его прежний отказ — и только его (`notices.ts`, проверка
  // прода 10.10.2026: красный «Смета не посчиталась» висел рядом с новой сметой).
  const estimate = useMutation({
    mutationFn: (asked: RunRequest) => estimateRun(asked),
    onSuccess: (made, asked) => {
      settle('run-estimate');
      setForecast(made);
      setAskedFor(JSON.stringify(asked));
    },
    onError: (failure) =>
      refuse('run-estimate', { title: 'Смета не посчиталась', message: refusalOf(failure) }),
  });

  const launch = useMutation({
    mutationFn: () => startRun(body),
    onSuccess: async (queued) => {
      settle('run-start');
      notify({ title: 'Прогон в очереди', message: queued.note, color: 'green' });
      setForecast(null);
      // Новый прогон встаёт первым на первой странице — туда и ведём, иначе
      // со второй страницы запуск выглядел бы как «ничего не случилось».
      // Сбрасывается весь `['runs']`: сдвинулись все страницы, а не одна.
      goToPage(1);
      await queryClient.invalidateQueries({ queryKey: ['runs'] });
    },
    onError: (failure) =>
      refuse('run-start', { title: 'Прогон не запущен', message: refusalOf(failure) }),
  });

  // Смета устаревает, как только меняют ключи, страну, глубину или потолок:
  // показывать старые числа рядом с новым вопросом — это обещание цены,
  // которой нет. Глубина — вдесятеро дороже на ста результатах, а до
  // 25.09.2026 устаревание смотрело только на число ключей.
  const stale = forecast !== null && askedFor !== JSON.stringify(body);
  const canRun = can('run');
  // Потолок с отказом в тело не попадает: без своей проверки запуск ушёл бы без потолка —
  // на весь остаток по капу, — а в поле стояло бы «1.5».
  const launchable =
    forecast?.affordable === true &&
    forecast.serp_refusal === null &&
    !stale &&
    canRun &&
    capRefusal === null;

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap={FIELD_GAP}>
          <PageHead
            title="Прогон"
            hint="Стоимость называется до того, как её потратят: сначала смета, потом кнопка. Пока смета не посчитана, запускать нечего — прогон без сверки с остатком это прямой путь к выбранному месячному лимиту за один день."
          />

          <Stack gap="xs">
            {/* Переключатель и секция сборки — без общего промежутка: отступ
                секции растёт вместе с её высотой, иначе поле ключей
                сдвигалось бы на десять пикселей рывком в первом кадре. */}
            <div>
              {/* По содержимому: два слова на 1 140 px во всю ширину читались полосой,
                  а не выбором (аудит экранов 09.10.2026). На телефоне — столбиком. */}
              <SegmentedControl
                orientation={segments}
                value={source}
                onChange={(value) => setSource(value as 'manual' | 'model' | 'competitors')}
                disabled={!canRun}
                data={[
                  { label: 'Свои ключи', value: 'manual' },
                  { label: 'Собрать моделью', value: 'model' },
                  { label: 'Конкуренты', value: 'competitors' },
                ]}
              />

              {/* Вложенный блок берёт `glassSolid`, а не `glassPanel`:
                  второй слой того же рецепта в тёмной теме выходит светлой
                  плитой, и весь приглушённый текст на ней выцветает —
                  намерено 2.46 : 1 при норме 4.5. */}
              <Unfold open={source === 'model'} className="glassSolid" p="md" mt="xs">
                <ModelPool state={modelPool} canRun={canRun} />
              </Unfold>
            </div>

            {source === 'competitors' ? (
              <CompetitorFields
                run={rivals}
                country={country}
                onCountry={setCountry}
                countries={countries.data ?? []}
                canRun={canRun}
              />
            ) : (
              <>
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
              </>
            )}
          </Stack>

          {source !== 'competitors' && (
            <Group align="flex-end" gap="md" className="fieldRow runFields">
              <Select
                label="Страна выдачи"
                description="Список приходит с сервера"
                w={{ base: '100%', xs: WIDTH.country }}
                searchable
                value={country}
                data={(countries.data ?? []).map((code) => ({
                  value: code,
                  label: countryTitle(code),
                }))}
                onChange={(value) => value !== null && setCountry(value)}
                comboboxProps={{ width: 'target', position: 'bottom-start' }}
              />
              <Select
                label="Глубина выдачи"
                description="На каждый ключ"
                w={{ base: '100%', xs: WIDTH.depth }}
                value={String(depth)}
                allowDeselect={false}
                data={DEPTHS.map((results) => ({
                  value: String(results),
                  label: depthTitle(results),
                }))}
                onChange={(value) => {
                  const chosen = DEPTHS.find((results) => String(results) === value);
                  if (chosen !== undefined) setDepth(chosen);
                }}
                comboboxProps={{ width: 'target', position: 'bottom-start' }}
              />
              {/* Слово владельца 14.10.2026: по умолчанию быстрый — выдача за секунды;
                  дешёвый ждёт в очереди провайдера минутами. Цена — в смете. */}
              <Select
                label="Режим выдачи"
                description="Быстрый втрое дороже"
                w={{ base: '100%', xs: WIDTH.serpMode }}
                value={serpMode}
                allowDeselect={false}
                data={SERP_MODES.map((mode) => ({ value: mode, label: SERP_MODE_TITLES[mode] }))}
                onChange={(value) => {
                  if (isSerpMode(value)) setSerpMode(value);
                }}
                comboboxProps={{ width: 'target', position: 'bottom-start' }}
              />
              {/* Своя планка на прогон: попробовать нишу дёшево, не сокращая
                  список ключей. Больше остатка по капу её всё равно не
                  поднять — сервер возьмёт меньшее из двух. */}
              <NumberField
                label="Потолок юнитов"
                description="Пусто — весь остаток по капу"
                w={{ base: '100%', xs: WIDTH.cap }}
                refusalAbove
                value={cap}
                error={capRefusal}
                onChange={setCap}
              />
              <Group gap="sm" className="runActions">
                <Button
                  variant="default"
                  className="press"
                  leftSection={<IconCalculator size={18} />}
                  loading={estimate.isPending}
                  disabled={list.length === 0 || capRefusal !== null}
                  onClick={() => estimate.mutate(body)}
                >
                  Посчитать смету
                </Button>
                <Button
                  className="press"
                  variant="gradient"
                  leftSection={<IconPlayerPlay size={18} />}
                  loading={launch.isPending}
                  disabled={!launchable}
                  onClick={() => launch.mutate()}
                >
                  Запустить
                </Button>
              </Group>
            </Group>
          )}

          {stale && source !== 'competitors' && (
            <Alert color="yellow" title="Смета устарела">
              Ключи, страна, глубина выдачи или потолок изменились после расчёта. Посчитайте смету
              заново — иначе кнопка обещает цену, которой уже нет.
            </Alert>
          )}
          {!canRun && (
            <Alert color="yellow" title="Запускать прогоны не разрешено">
              Смету посмотреть можно, запуск тратит юниты и выдан отдельным правом.
            </Alert>
          )}
        </Stack>
      </Card>

      {/* Словами человека, без команд (проверка QA 10.10.2026: экран велел набрать
          `python -m backend.workers.reaper`); администратору — строка с именем службы. */}
      {waiting && view?.workers === 0 && (
        <Alert color="yellow" title="Задачу некому взять">
          Прогон стоит в очереди, но служба, которая выполняет прогоны, сейчас не запущена: пока её
          не запустят, прогон не начнётся. Сервер при этом отвечает «поставлено», и со стороны всё
          выглядит исправным. Сообщите администратору — службу запускают на сервере.
          <Text size="xs" c="dimmed" mt={6}>
            Для администратора: не запущена служба worker — очередь задач некому разбирать.
          </Text>
        </Alert>
      )}

      {source === 'competitors' && <CompetitorEstimate run={rivals} />}
      {forecast !== null && !stale && source !== 'competitors' && (
        <Card className="glass" p="lg">
          <Title order={5} mb="sm">
            Смета
          </Title>
          <Estimate forecast={forecast} lines={list.length} />
        </Card>
      )}

      <RunHistory view={view} page={page} onPage={goToPage} />
    </Stack>
  );
}
