/**
 * «Конкуренты» — третий вход прогона рядом со «Своими ключами» и «Собрать моделью».
 *
 * Слова оператора: «указываем сайты конкурентов, и оно парсит их ссылочную массу, отбирает
 * хорошие площадки». Сайт, который ссылается на конкурента, уже ставит ссылки сайтам ниши.
 * Ahrefs отдаёт только тех, кто проходит пороги DR и трафика, остальное проверяет та же
 * воронка, что у ключей (`backend/features/runs/competitors.py`).
 *
 * **Цена — до кнопки и словами того же счёта.** Списки платятся юнитами Ahrefs — того же
 * ключа, что метрики, и в смете они отдельной плиткой рядом с метриками сайтов: ключ общий
 * с соседней системой, и «во что обойдётся» должно читаться без арифметики. Как у ключей:
 * смета — худший случай, кнопка не нажимается без неё, а смена любого поля смету снимает.
 *
 * Своим файлом, а не строками `RunPage`: экран прогона держит переключатель входа, этот
 * файл — состояние, поля и смету своего входа (`useCompetitorRun`).
 */

import {
  Alert,
  Button,
  Card,
  Group,
  Select,
  SimpleGrid,
  Stack,
  TagsInput,
  Text,
  Title,
} from '@mantine/core';
import { IconCalculator, IconPlayerPlay } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { estimateCompetitors, startCompetitors } from '../api/competitors';
import type { CompetitorForecast, CompetitorRunBody } from '../api/competitors';
import { countryTitle } from '../api/labels';
import { Metric } from '../components/Metric';
import { NumberField } from '../components/NumberField';
import { numberRefusal, validNumber } from '../components/numberText';
import type { NumberRule } from '../components/numberText';
import { formatNumber, plural } from '../format';
import { notify, refuse, settle } from '../notices';
import { TopicTokens } from '../topics/TopicNotes';
import { BudgetTile, BudgetVerdict, since } from './BudgetVerdict';

/** Конкурентов за прогон — не больше, как у сервера (`competitors.MAX_COMPETITORS`). */
export const MAX_RIVALS = 5;
/** Что предлагает список глубины: ссылающихся доменов на конкурента, сильнейшие первыми.
 *  Сервер принимает от 10 до 1000; глубже пятисот один конкурент съедает десятки тысяч
 *  юнитов на метрики, и такой выбор делается в консоли, а не одним щелчком. */
export const RIVAL_DEPTHS = [50, 100, 200, 500] as const;
const DEFAULT_RIVAL_DEPTH = 100;
/** Потолок юнитов — то же правило, что у прогона по ключам (`RunPage`, `CAP`). */
const CAP: NumberRule = { decimals: 0, min: 1, max: 1_000_000 };
/** Ширины — по самому длинному значению: «Саудовская Аравия», «500 сайтов на конкурента». */
const WIDTH = { country: 200, depth: 240, cap: 190 } as const;

function sitesTitle(count: number): string {
  return `${formatNumber(count)} ${plural(count, 'сайт', 'сайта', 'сайтов')}`;
}

export interface CompetitorRun {
  rivals: string[];
  setRivals: (next: string[]) => void;
  depth: number;
  setDepth: (next: number) => void;
  cap: string;
  setCap: (next: string) => void;
  capRefusal: string | null;
  forecast: CompetitorForecast | null;
  /** Смета посчитана по другим полям: показывать её числа — обещать цену, которой нет. */
  stale: boolean;
  estimating: boolean;
  launching: boolean;
  estimate: () => void;
  launch: () => void;
}

/** Состояние входа «Конкуренты»: поля, смета и запуск. `onLaunched` — что сделать экрану
 *  после постановки (вести на первую страницу истории). */
export function useCompetitorRun(country: string, onLaunched: () => void): CompetitorRun {
  const queryClient = useQueryClient();
  const [rivals, setRivals] = useState<string[]>([]);
  const [depth, setDepth] = useState<number>(DEFAULT_RIVAL_DEPTH);
  const [cap, setCap] = useState('');
  const [forecast, setForecast] = useState<CompetitorForecast | null>(null);
  const [askedFor, setAskedFor] = useState<string | null>(null);

  const capRefusal = numberRefusal(cap, CAP);
  const capValue = validNumber(cap, CAP);
  const body: CompetitorRunBody = {
    competitors: rivals,
    country,
    depth,
    ...(capValue !== null ? { cap: capValue } : {}),
  };

  // Удача действия снимает его прежний отказ — и только его (`notices.ts`).
  const estimating = useMutation({
    mutationFn: (asked: CompetitorRunBody) => estimateCompetitors(asked),
    onSuccess: (made, asked) => {
      settle('rivals-estimate');
      setForecast(made);
      setAskedFor(JSON.stringify(asked));
    },
    onError: (failure) =>
      refuse('rivals-estimate', { title: 'Смета не посчиталась', message: refusalOf(failure) }),
  });
  const launching = useMutation({
    mutationFn: () => startCompetitors(body),
    onSuccess: async (queued) => {
      settle('rivals-start');
      notify({ title: 'Прогон в очереди', message: queued.note, color: 'green' });
      setForecast(null);
      onLaunched();
      await queryClient.invalidateQueries({ queryKey: ['runs'] });
    },
    onError: (failure) =>
      refuse('rivals-start', { title: 'Прогон не запущен', message: refusalOf(failure) }),
  });

  return {
    rivals,
    setRivals,
    depth,
    setDepth,
    cap,
    setCap,
    capRefusal,
    forecast,
    stale: forecast !== null && askedFor !== JSON.stringify(body),
    estimating: estimating.isPending,
    launching: launching.isPending,
    estimate: () => estimating.mutate(body),
    launch: () => launching.mutate(),
  };
}

/** Отказ потолка модели из сметы; поля нет (ответ сервера до этого поля) — отказа нет. */
function modelRefusal(forecast: CompetitorForecast | null): string | null {
  return forecast?.llm_refusal ?? null;
}

interface FieldsProps {
  run: CompetitorRun;
  country: string;
  onCountry: (code: string) => void;
  countries: string[];
  canRun: boolean;
}

/** Поля входа «Конкуренты» и кнопки: без сметы, с устаревшей или непомещающейся — не
 *  запускается, как у ключей. */
export function CompetitorFields({ run, country, onCountry, countries, canRun }: FieldsProps) {
  const fits = run.forecast?.affordable === true && modelRefusal(run.forecast) === null;
  const launchable = fits && !run.stale && canRun && run.capRefusal === null;
  return (
    <Stack gap="xs">
      <TagsInput
        label="Сайты конкурентов"
        description={`До ${MAX_RIVALS} доменов — Enter или запятая после каждого`}
        placeholder={run.rivals.length === 0 ? 'example.com' : ''}
        value={run.rivals}
        onChange={run.setRivals}
        maxTags={MAX_RIVALS}
        splitChars={[',', ' ', ';', '\n']}
        clearable
      />
      <Text size="sm" c="dimmed">
        Возьмём сайты, которые ссылаются на этих конкурентов и проходят пороги DR и трафика. Списки
        платятся юнитами Ahrefs — того же ключа, что метрики.
      </Text>
      <Group align="flex-end" gap="md" className="fieldRow runFields">
        <Select
          label="Страна прогона"
          description="По ней — регион донора"
          w={{ base: '100%', xs: WIDTH.country }}
          searchable
          value={country}
          data={countries.map((code) => ({ value: code, label: countryTitle(code) }))}
          onChange={(value) => value !== null && onCountry(value)}
          comboboxProps={{ width: 'target', position: 'bottom-start' }}
        />
        <Select
          label="Глубина списка"
          description="Сильнейшие по DR — первыми"
          w={{ base: '100%', xs: WIDTH.depth }}
          value={String(run.depth)}
          allowDeselect={false}
          data={RIVAL_DEPTHS.map((depth) => ({
            value: String(depth),
            label: `${sitesTitle(depth)} на конкурента`,
          }))}
          onChange={(value) => value !== null && run.setDepth(Number(value))}
          comboboxProps={{ width: 'target', position: 'bottom-start' }}
        />
        <NumberField
          label="Потолок юнитов"
          description="Пусто — весь остаток по капу"
          w={{ base: '100%', xs: WIDTH.cap }}
          refusalAbove
          value={run.cap}
          error={run.capRefusal}
          onChange={run.setCap}
        />
        <Group gap="sm" className="runActions">
          <Button
            variant="default"
            className="press"
            leftSection={<IconCalculator size={18} />}
            loading={run.estimating}
            disabled={run.rivals.length === 0 || run.capRefusal !== null}
            onClick={run.estimate}
          >
            Посчитать смету
          </Button>
          <Button
            className="press"
            variant="gradient"
            leftSection={<IconPlayerPlay size={18} />}
            loading={run.launching}
            disabled={!launchable}
            onClick={run.launch}
          >
            Запустить
          </Button>
        </Group>
      </Group>
      {run.stale && (
        <Alert color="yellow" title="Смета устарела">
          Конкуренты, страна, глубина списка или потолок изменились после расчёта. Посчитайте смету
          заново — иначе кнопка обещает цену, которой уже нет.
        </Alert>
      )}
    </Stack>
  );
}

/** Сколько списков и почём строка — тем же словом, что поле глубины. */
function listsHint(forecast: CompetitorForecast): string {
  const count = forecast.lists.length;
  const rivals = `${count} ${plural(count, 'конкурент', 'конкурента', 'конкурентов')}`;
  return `${rivals} × до ${sitesTitle(forecast.depth)} · ${forecast.row_units} юн. за строку`;
}

/** Модель — другой счёт: токены, а не юниты; судья выключен — так и сказано. */
function modelLine(forecast: CompetitorForecast): string {
  return forecast.tokens > 0
    ? `Судья площадок потратит до ${formatNumber(forecast.tokens)} токенов модели — это другой счёт, не юниты Ahrefs.`
    : 'Судья площадок выключен — модель не тратится.';
}

/** Смета входа «Конкуренты»: списки, сайты списков, всё в юнитах и бюджет — плитками,
 *  модель и наш расход за цикл — строкой, «помещается / не хватает» — плашкой. Суд не
 *  помещается в потолок модели — своей плашкой, и зелёного «помещается» рядом нет. */
export function CompetitorEstimate({ run }: { run: CompetitorRun }) {
  const forecast = run.forecast;
  if (forecast === null || run.stale) return null;
  const refusal = modelRefusal(forecast);
  return (
    <Card className="glass" p="lg">
      <Title order={5} mb="sm">
        Смета
      </Title>
      <Stack gap="sm">
        <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
          <Metric
            title="Списки конкурентов"
            value={`до ${formatNumber(forecast.units_lists)}`}
            hint={listsHint(forecast)}
          />
          <Metric
            title="Сайтов на проверку"
            value={`до ${formatNumber(forecast.expected_domains)}`}
            hint="все новые — худший случай"
          />
          <Metric
            title="Юнитов Ahrefs"
            value={`до ${formatNumber(forecast.units_total)}`}
            hint={`списки ${formatNumber(forecast.units_lists)} · просев ${formatNumber(forecast.units_screen)} · метрики ${formatNumber(forecast.units_metrics)} · гео ${formatNumber(forecast.units_by_country)}`}
          />
          <BudgetTile figures={forecast} />
        </SimpleGrid>
        <Text size="sm" c="dimmed">
          {modelLine(forecast)} Потрачено нами юнитов {since(forecast)}:{' '}
          <b>{formatNumber(forecast.units_spent_in_cycle)}</b>.
        </Text>
        <TopicTokens forecast={forecast} />
        {refusal !== null && (
          <Alert color="red" title="Не помещается в потолок модели">
            {refusal}.
          </Alert>
        )}
        <BudgetVerdict
          figures={forecast}
          blocked={refusal !== null}
          trim="Или уберите конкурента, или уменьшите глубину списка."
          fits="Смета считает худший случай — каждый список до глубины и все сайты новые. За сайты, у которых данные ещё свежие, второй раз не платят, а уже принятых доноров прогон не берёт, поэтому по факту обычно меньше."
        />
      </Stack>
    </Card>
  );
}
