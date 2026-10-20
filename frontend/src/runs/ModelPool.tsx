/**
 * «Собрать моделью» — второй режим поля ключей экрана прогона: модель собирает пул по
 * теме словами или по примерам сайтов (`ExamplesPool`), фразы падают в поле ключей,
 * и до сметы их можно править. Смета и кап остаются последним рубежом перед тратой.
 *
 * **Своим файлом** (20.10.2026): экран прогона стоял у предела длины файла, а сборка
 * по примерам добавила режиму вторую основу. Состояние режима — в хуке
 * (`useModelPool`), который держит экран: закрытая секция не отрисовывается
 * (`Unfold`), а набранные тема и примеры при этом не теряются.
 *
 * **Поля — компактным ровным рядом**, как у формы прогона: ширина поля — по самому
 * длинному значению шрифтом экрана, на телефоне — во всю ширину (`runFields`).
 */

import { Button, Group, SegmentedControl, Select, Stack, TagsInput, Text } from '@mantine/core';
import { IconSparkles } from '@tabler/icons-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { buildPool, fetchMarketLanguages, fetchPresets } from '../api/keywords';
import { languageTitle, presetTitle } from '../api/labels';
import { NumberField } from '../components/NumberField';
import { numberRefusal, validNumber } from '../components/numberText';
import type { NumberRule } from '../components/numberText';
import { notify, refuse, settle } from '../notices';
import { keywordsTitle } from './depth';
import { ExamplesField, ExamplesOutcome, POOL_BASES, useExamplesBuild } from './ExamplesPool';
import type { PoolBasis } from './ExamplesPool';
import { countExamples, examplesRefusal } from './examples';

/** Набор углов, пока человек не выбрал другой, — тот же, что берёт сервер
 *  по умолчанию (`angles.DEFAULT_PRESET`). */
const DEFAULT_PRESET = 'wide';

/** Сколько ключей собрать — целое: «1.5» получает отказ, а не становится 15. */
const POOL_CAP: NumberRule = { decimals: 0, min: 1, max: 100 };

/** Ширины полей ряда — по самому длинному значению шрифтом экрана: «инструкции
 *  и правила» в наборе углов. */
const WIDTH = { preset: 206, topics: 260, poolCap: 140 } as const;

interface Options {
  country: string;
  /** Секция открыта: пресеты и языки спрашиваются только тогда. */
  open: boolean;
  /** Собранные фразы — в поле ключей экрана. */
  onKeywords: (keywords: string[]) => void;
}

/** Состояние режима сборки моделью: поля, справочники сервера и обе сборки. */
export function useModelPool({ country, open, onKeywords }: Options) {
  const [preset, setPreset] = useState<string>(DEFAULT_PRESET);
  const [topics, setTopics] = useState<string[]>([]);
  const [poolCap, setPoolCap] = useState('30');
  const [basis, setBasis] = useState<PoolBasis>('topic');
  const [examples, setExamples] = useState('');
  // Пресеты спрашиваются у сервера по той же причине, что и страны:
  // второй список на фронте разъехался бы с первым добавленным набором,
  // и разошёлся бы молча.
  const presets = useQuery({ queryKey: ['presets'], queryFn: fetchPresets, enabled: open });
  // Языки выводятся из страны, а не выбираются: оператору нечем ошибиться,
  // а незнакомая страна отказывает вслух вместо тихого английского.
  const marketLanguages = useQuery({
    queryKey: ['market-languages', country],
    queryFn: () => fetchMarketLanguages(country),
    enabled: open,
  });
  const cap = validNumber(poolCap, POOL_CAP) ?? 30;

  const pool = useMutation({
    mutationFn: () => buildPool({ preset, country, topics, cap }),
    onSuccess: (built) => {
      settle('run-pool');
      // Фразы падают в то же поле, а не уходят в прогон: человек видит
      // их и правит до сметы. Смета и кап остаются последним рубежом.
      onKeywords(built.keywords);
      notify({
        title: `Собрано ${keywordsTitle(built.keywords.length)}`,
        message:
          built.refusals.length > 0
            ? `Модель ${built.model} отказала ${built.refusals.length} раз(а) — пул неполный: ${built.refusals[0]}`
            : `Модель ${built.model}, ${built.tokens} токенов, языки: ${built.languages.map(languageTitle).join(', ')}. Проверьте список перед сметой.`,
        color: built.refusals.length > 0 ? 'yellow' : 'green',
      });
    },
    onError: (failure) =>
      refuse('run-pool', { title: 'Пул не собрался', message: refusalOf(failure) }),
  });
  // Ключи по примерам падают в то же поле, что по теме.
  const fromExamples = useExamplesBuild((built) => onKeywords(built.keywords));

  return {
    country,
    cap,
    preset,
    setPreset,
    topics,
    setTopics,
    poolCap,
    setPoolCap,
    basis,
    setBasis,
    examples,
    setExamples,
    presets,
    marketLanguages,
    pool,
    fromExamples,
  };
}

export type ModelPoolState = ReturnType<typeof useModelPool>;

interface Props {
  state: ModelPoolState;
  canRun: boolean;
}

/** Языки рынка — до сборки: на двух языках пул стоит вдвое дороже, и узнавать об этом
 *  по счёту неправильно. */
function MarketLanguages({ query }: { query: ModelPoolState['marketLanguages'] }) {
  const languages = query.data ?? [];
  const named = languages.map(languageTitle).join(', ') || '…';
  return (
    <Text size="sm" c="dimmed">
      {query.isError
        ? 'Язык этого рынка не выводится — сборка откажет и скажет почему.'
        : `${languages.length === 1 ? 'Язык' : 'Языки'} рынка: ${named}`}
    </Text>
  );
}

interface ButtonProps extends Props {
  /** Почему собирать нельзя — отказ поля числа ключей или поля примеров. */
  blocked: boolean;
}

/** Одна кнопка на обе основы: «Собрать» по теме или «Собрать по примерам». */
function BuildButton({ state, canRun, blocked }: ButtonProps) {
  const byExamples = state.basis === 'examples';
  const build = () =>
    byExamples
      ? state.fromExamples.mutate({
          examples: state.examples.split('\n'),
          preset: state.preset,
          country: state.country,
          cap: state.cap,
        })
      : state.pool.mutate();
  return (
    <Button
      variant="light"
      className="press"
      leftSection={<IconSparkles size={16} />}
      onClick={build}
      loading={state.pool.isPending || state.fromExamples.isPending}
      disabled={!canRun || blocked}
    >
      {byExamples ? 'Собрать по примерам' : 'Собрать'}
    </Button>
  );
}

/** Почему собирать нельзя: число ключей с отказом или, у примеров, поле с отказом или пустое. */
function blockedBy(state: ModelPoolState, examplesProblem: string | null, sites: number): boolean {
  if (numberRefusal(state.poolCap, POOL_CAP) !== null) return true;
  return state.basis === 'examples' && (examplesProblem !== null || sites === 0);
}

/** Содержимое секции «Собрать моделью»: пояснение, языки рынка, основа сборки и ряд полей. */
export function ModelPool({ state, canRun }: Props) {
  const byExamples = state.basis === 'examples';
  const examplesProblem = byExamples ? examplesRefusal(state.examples) : null;
  const sites = countExamples(state.examples).sites;

  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">
        Сборка ничего платного не тратит — только модель. Фразы попадут в поле ниже, и до сметы их
        можно править: смета и кап остаются последним рубежом перед тратой.
      </Text>
      <MarketLanguages query={state.marketLanguages} />
      {/* По содержимому, как переключатель режима над секцией: во всю ширину два
          слова читались бы полосой, а не выбором (аудит экранов 09.10.2026). */}
      <div>
        <SegmentedControl
          size="xs"
          value={state.basis}
          onChange={(value) => state.setBasis(value as PoolBasis)}
          disabled={!canRun}
          data={POOL_BASES}
          aria-label="Основа сборки"
        />
      </div>
      {byExamples ? (
        <ExamplesField
          value={state.examples}
          onChange={state.setExamples}
          refusal={examplesProblem}
        />
      ) : null}
      <Group align="flex-end" gap="md" className="fieldRow runFields">
        <Select
          label="Набор углов"
          description="Только обкатанные наборы"
          data={(state.presets.data ?? [DEFAULT_PRESET]).map((code) => ({
            value: code,
            label: presetTitle(code),
          }))}
          value={state.preset}
          allowDeselect={false}
          onChange={(value) => value !== null && state.setPreset(value)}
          comboboxProps={{ width: 'target', position: 'bottom-start' }}
          w={{ base: '100%', xs: WIDTH.preset }}
        />
        {byExamples ? null : <TopicsField state={state} />}
        <NumberField
          label="Сколько ключей"
          description="От 1 до 100"
          refusalAbove
          value={state.poolCap}
          error={numberRefusal(state.poolCap, POOL_CAP)}
          onChange={state.setPoolCap}
          w={{ base: '100%', xs: WIDTH.poolCap }}
        />
        <BuildButton
          state={state}
          canRun={canRun}
          blocked={blockedBy(state, examplesProblem, sites)}
        />
      </Group>
      {byExamples ? (
        <ExamplesOutcome
          built={state.fromExamples.data}
          pending={state.fromExamples.isPending}
          sites={sites}
        />
      ) : null}
    </Stack>
  );
}

/** «Про что» — темы словами, основа сборки по теме. */
function TopicsField({ state }: { state: ModelPoolState }) {
  return (
    <TagsInput
      label="Про что"
      description="Несколько тем — больше доменов"
      placeholder={state.topics.length === 0 ? 'ставки на спорт' : ''}
      value={state.topics}
      onChange={state.setTopics}
      clearable
      w={{ base: '100%', xs: WIDTH.topics }}
    />
  );
}
