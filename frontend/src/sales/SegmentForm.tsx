/**
 * Новый прогон гипотезы: ключи, рынок выдачи, язык писем, глубина и режим выдачи — смета до
 * запуска.
 *
 * **Смета — до кнопки «Запустить», и считает её сервер** (`POST /sales/segment/estimate`):
 * выдача с ценой страницы и ожиданием по режиму, сайты, модель в токенах и деньгах, Hunter и
 * деньги вместе — строкой; взятые ключи с пометками и отсеянные с причиной; отказ словами —
 * выше остатка дневного потолка модели, нет описания сегмента, песочница выдачи. Запуск
 * открыт только по смете тех же значений формы **и того же описания сегмента**: правка поля
 * или описания снимает смету — судья судит по описанию, и запустить то, чего не видел,
 * нельзя. Сервер на запуске считает её заново (`POST /sales/segment/runs`) и отказывает теми
 * же словами.
 *
 * **Режим выдачи по умолчанию — быстрый** (Live, слово владельца 14.10): секунды вместо минут
 * очереди. «Стандарт» — дешевле и дольше, на выбор; приоритетный форма не предлагает.
 *
 * **Право — «run»**: выдача, модель и платный поиск адресов — траты, как прогон доноров и
 * очистка лидов. Без права смету видно, а у кнопки — кто запускает.
 */

import { Alert, Button, Group, Select, Stack, Text, Textarea, Title } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { countryTitle, PERMISSION_TITLES } from '../api/labels';
import { useSession } from '../auth/AuthProvider';
import { formatNumber } from '../format';
import { notify } from '../notices';
import { dropdownBelow } from '../theme';
import { estimateRun, startRun } from './segmentApi';
import type { SegmentAsk, SegmentEstimate, SegmentHome, SerpMode } from './segmentApi';
import { DEPTHS, depthTitle, LANGUAGE_TITLES } from './segmentData';
import { KeywordLists } from './SegmentKeywords';

/** Рынок, пока человек не выбрал: англоязычная выдача — гипотеза 2. */
const DEFAULT_COUNTRY = 'us';
/** Язык писем по рынку — пустым значением: его называет сервер в смете. */
const BY_MARKET = '';
/** Режим выдачи, пока человек не выбрал: быстрый (слово владельца 14.10). */
const DEFAULT_SERP_MODE: SerpMode = 'live';
/** Режимы формы: цену страницы и ожидание называет смета, а не подпись. */
const SERP_MODES: { value: SerpMode; label: string }[] = [
  { value: 'live', label: 'Быстрая (Live)' },
  { value: 'standard', label: 'Стандарт — дешевле, дольше' },
];

interface Draft {
  keywords: string;
  country: string;
  language: string;
  depth: number;
  serpMode: SerpMode;
}

function askOf(hypothesis: number, draft: Draft): SegmentAsk {
  return {
    hypothesis_id: hypothesis,
    keywords: draft.keywords,
    country: draft.country,
    language: draft.language === BY_MARKET ? null : draft.language,
    depth_pages: draft.depth,
    serp_mode: draft.serpMode,
  };
}

/** Что считали: значения формы и описание сегмента — смета годна, пока не изменилось ни то,
 *  ни другое. */
interface Counted {
  ask: SegmentAsk;
  segment: string | null;
}

/** Смета словами сервера и отказ, если запускать нельзя. */
function EstimateBlock({ found }: { found: SegmentEstimate }) {
  return (
    <Stack gap={6} aria-label="Смета прогона">
      {found.lines.length > 0 && (
        <Stack gap={2} className="segmentEstimate">
          {found.lines.map((line) => (
            <Text key={line} size="sm">
              {line}
            </Text>
          ))}
        </Stack>
      )}
      <KeywordLists taken={found.keywords} notes={found.notes} rejected={found.rejected} />
      {found.refusal !== null && (
        <Alert color="red" title="Прогон не запустится">
          {found.refusal}
        </Alert>
      )}
    </Stack>
  );
}

function Fields({
  home,
  draft,
  onDraft,
}: {
  home: SegmentHome;
  draft: Draft;
  onDraft: (patch: Partial<Draft>) => void;
}) {
  return (
    <>
      <Textarea
        label="Ключи"
        description={`По ключу на строку, не больше ${formatNumber(home.max_keywords)}: мусор и точные повторы отсеиваются до выдачи, почти повтор остаётся с пометкой.`}
        autosize
        minRows={4}
        maxRows={12}
        value={draft.keywords}
        onChange={(event) => onDraft({ keywords: event.currentTarget.value })}
      />
      <Group gap="md" align="flex-start" wrap="wrap">
        <Select
          label="Рынок выдачи"
          searchable
          allowDeselect={false}
          data={home.countries.map((code) => ({ value: code, label: countryTitle(code) }))}
          value={draft.country}
          onChange={(value) => onDraft({ country: value ?? DEFAULT_COUNTRY })}
          comboboxProps={dropdownBelow}
          w={{ base: '100%', xs: 220 }}
        />
        <Select
          label="Язык писем"
          allowDeselect={false}
          data={[
            { value: BY_MARKET, label: 'язык рынка' },
            ...home.languages.map((code) => ({
              value: code,
              label: LANGUAGE_TITLES[code] ?? code,
            })),
          ]}
          value={draft.language}
          onChange={(value) => onDraft({ language: value ?? BY_MARKET })}
          comboboxProps={dropdownBelow}
          w={{ base: '100%', xs: 180 }}
        />
        <Select
          label="Глубина выдачи"
          allowDeselect={false}
          data={DEPTHS.filter((pages) => pages <= home.max_depth).map((pages) => ({
            value: String(pages),
            label: depthTitle(pages),
          }))}
          value={String(draft.depth)}
          onChange={(value) => onDraft({ depth: Number(value ?? 1) })}
          comboboxProps={dropdownBelow}
          w={{ base: '100%', xs: 240 }}
        />
        <Select
          label="Выдача"
          allowDeselect={false}
          data={SERP_MODES}
          value={draft.serpMode}
          onChange={(value) =>
            onDraft({ serpMode: (value as SerpMode | null) ?? DEFAULT_SERP_MODE })
          }
          comboboxProps={dropdownBelow}
          w={{ base: '100%', xs: 250 }}
        />
      </Group>
    </>
  );
}

interface Props {
  home: SegmentHome;
  onStarted: (run: number) => void;
}

export function SegmentForm({ home, onStarted }: Props) {
  const { can } = useSession();
  const [draft, setDraft] = useState<Draft>({
    keywords: '',
    country: DEFAULT_COUNTRY,
    language: BY_MARKET,
    depth: 1,
    serpMode: DEFAULT_SERP_MODE,
  });
  const ask = askOf(home.hypothesis_id, draft);
  const asked = JSON.stringify({ ask, segment: home.segment } satisfies Counted);
  // Смета — для тех значений формы и того описания сегмента, по которым её считали: правка
  // поля или описания её снимает.
  const [counted, setCounted] = useState<{ key: string; found: SegmentEstimate } | null>(null);
  const found = counted !== null && counted.key === asked ? counted.found : null;
  const count = useMutation({
    mutationFn: (sent: Counted) => estimateRun(sent.ask),
    onSuccess: (estimate, sent) => setCounted({ key: JSON.stringify(sent), found: estimate }),
  });
  const start = useMutation({
    mutationFn: (sent: SegmentAsk) => startRun(sent),
    onSuccess: (queued) => {
      notify({ message: `Прогон №${queued.run_id} ушёл в очередь задач`, color: 'green' });
      setCounted(null);
      onStarted(queued.run_id);
    },
  });
  const ready = found !== null && found.refusal === null;
  return (
    <Stack gap="sm">
      <Title order={5}>Новый прогон</Title>
      <Fields
        home={home}
        draft={draft}
        onDraft={(patch) => {
          start.reset();
          setDraft((current) => ({ ...current, ...patch }));
        }}
      />
      <Group gap="sm" wrap="wrap">
        <Button
          variant="default"
          className="press"
          loading={count.isPending}
          disabled={draft.keywords.trim() === ''}
          onClick={() => count.mutate({ ask, segment: home.segment })}
        >
          Посчитать смету
        </Button>
        {can('run') ? (
          <Button
            className="press"
            disabled={!ready}
            loading={start.isPending}
            onClick={() => start.mutate(ask)}
          >
            Запустить прогон
          </Button>
        ) : (
          <Text size="sm" c="dimmed">
            Запускает сотрудник с правом «{PERMISSION_TITLES.run}».
          </Text>
        )}
      </Group>
      {count.error !== null && (
        <Alert color="red" title="Смета не посчитана">
          {refusalOf(count.error)}
        </Alert>
      )}
      {found !== null && <EstimateBlock found={found} />}
      {start.error !== null && (
        <Alert color="red" title="Прогон не запущен">
          {refusalOf(start.error)}
        </Alert>
      )}
    </Stack>
  );
}
