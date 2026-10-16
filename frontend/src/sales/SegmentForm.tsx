/**
 * Новый прогон гипотезы: ключи, рынок выдачи, язык писем, глубина и режим выдачи — смета до
 * запуска.
 *
 * **Свёрнута в кнопку «Новый прогон», если у гипотезы уже есть прогоны** (волна 15: форма и
 * описание сегмента стояли над отчётом при каждом заходе, прогон размера №1 занимал 4,6 тысячи
 * пикселей на 1440): работа на вкладке с прогонами — читать отчёт, а запускают раз в несколько
 * дней. Прогонов нет — форма раскрыта и кнопки нет: запустить — следующее, что здесь делают. Так
 * же свёрнута сборка очереди на «Письмах»; запущенный прогон форму сворачивает.
 *
 * **Смета — до кнопки «Запустить», и считает её сервер** (`POST /sales/segment/estimate`):
 * ключи чипами — взятые с пометками, отсеянные с причиной (`SegmentKeywords`); выдача с ценой
 * страницы и ожиданием по режиму, сайты, модель в токенах и деньгах, Hunter и деньги вместе —
 * строкой; отказ словами — выше остатка дневного потолка модели, нет описания сегмента,
 * песочница выдачи. Правка поля или описания снимает смету — судья судит по описанию, и
 * запустить то, чего не видел, нельзя.
 *
 * **У взятого ключа — галочка, по умолчанию стоит.** Сняли — ключ не покупается: смета
 * пересчитывается сама, без кнопки, через полсекунды после последнего щелчка (`SegmentFormEstimate`);
 * пока не пересчитана, запуск закрыт, а снятый уже стоит «снят вами». Снимают номер строки, как вписана
 * (сервер не сдвигает «повтор строки N»); правка поля ключей снятые не теряет — номер ищется
 * заново по тексту строки (`segmentKeys`).
 *
 * **Запуск — только по смете, которую видел человек**: с запуском уходит отпечаток сметы, и
 * сервер, посчитав смету заново, сверяет его — ключи, снятые, описание сегмента, режимы; иначе
 * «смета устарела — посчитайте заново» (и вкладка перечитывает описание). Смета, посчитанная по
 * другому описанию, чем на экране (его поправили в соседней вкладке), не показывается: описание
 * перечитывается, смету считают заново.
 *
 * **Режим выдачи по умолчанию — настройкой сервиса** (`SegmentHome.serp_mode`, `SERP_MODE`): форма
 * не держит своё умолчание поверх настройки администратора. Сервер её не назвал (старше режимов
 * или настройка не прочитана) — быстрый (Live, слово владельца 14.10): секунды вместо минут
 * очереди. «Стандарт» — дешевле и дольше, на выбор; приоритетный форма предлагает, только когда
 * он умолчание сервиса: иначе поле стояло бы пустым.
 *
 * **Право — «run»**: выдача, модель и платный поиск адресов — траты, как прогон доноров и
 * очистка лидов. Без права смету видно, а у кнопки — кто запускает.
 */

import {
  Alert,
  Button,
  Collapse,
  Group,
  Select,
  Stack,
  Text,
  Textarea,
  Title,
} from '@mantine/core';
import { IconChevronDown } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useId, useState } from 'react';

import { refusalOf } from '../api/client';
import { countryTitle, PERMISSION_TITLES } from '../api/labels';
import { useSession } from '../auth/AuthProvider';
import { formatNumber } from '../format';
import { notify } from '../notices';
import { dropdownBelow } from '../theme';
import { startRun } from './segmentApi';
import type { SegmentAsk, SegmentHome, SerpMode } from './segmentApi';
import { DEPTHS, depthTitle, LANGUAGE_TITLES, SEGMENT_QUERY_KEY } from './segmentData';
import { EstimateBlock, useEstimate } from './SegmentFormEstimate';
import type { Asked } from './SegmentFormEstimate';
import { excludedLines, toggled } from './segmentKeys';
import type { Dropped } from './segmentKeys';

/** Рынок, пока человек не выбрал: англоязычная выдача — гипотеза 2. */
const DEFAULT_COUNTRY = 'us';
/** Язык писем по рынку — пустым значением: его называет сервер в смете. */
const BY_MARKET = '';
/** Режим выдачи, если сервер не назвал своего: быстрый (слово владельца 14.10). */
const FALLBACK_SERP_MODE: SerpMode = 'live';
/** Режимы словами: цену страницы и ожидание называет смета, а не подпись. */
const SERP_MODE_TITLES: Record<SerpMode, string> = {
  live: 'Быстрая (Live)',
  standard: 'Стандарт — дешевле, дольше',
  priority: 'Приоритет — быстрее, дороже',
};
/** Режимы, которые форма предлагает всегда. */
const OFFERED: SerpMode[] = ['live', 'standard'];
/** Режим выдачи, пока человек не выбрал: умолчание сервиса. */
function serpDefault(home: SegmentHome): SerpMode {
  return home.serp_mode ?? FALLBACK_SERP_MODE;
}

/** Режимы поля: быстрый и стандарт, и умолчание сервиса, если оно другое (приоритетный). */
function serpModes(home: SegmentHome): { value: SerpMode; label: string }[] {
  const fallback = serpDefault(home);
  const modes = OFFERED.includes(fallback) ? OFFERED : [...OFFERED, fallback];
  return modes.map((value) => ({ value, label: SERP_MODE_TITLES[value] }));
}

interface Draft {
  keywords: string;
  country: string;
  language: string;
  depth: number;
  serpMode: SerpMode;
}

function askOf(hypothesis: number, draft: Draft, excluded: number[]): SegmentAsk {
  return {
    hypothesis_id: hypothesis,
    keywords: draft.keywords,
    country: draft.country,
    language: draft.language === BY_MARKET ? null : draft.language,
    depth_pages: draft.depth,
    serp_mode: draft.serpMode,
    excluded,
  };
}

function asked(
  hypothesis: number,
  draft: Draft,
  excluded: number[],
  segment: string | null,
): Asked {
  const ask = askOf(hypothesis, draft, excluded);
  return {
    ask,
    segment,
    key: JSON.stringify({ ask, segment }),
    base: JSON.stringify({ ask: { ...ask, excluded: [] }, segment }),
  };
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
          data={serpModes(home)}
          value={draft.serpMode}
          onChange={(value) =>
            onDraft({ serpMode: (value as SerpMode | null) ?? serpDefault(home) })
          }
          comboboxProps={dropdownBelow}
          w={{ base: '100%', xs: 250 }}
        />
      </Group>
    </>
  );
}

/** Строка свёрнутой формы: кнопка с шевроном — как «Сборка очереди» на «Письмах». */
function FoldButton({
  shown,
  formId,
  onToggle,
}: {
  shown: boolean;
  formId: string;
  onToggle: () => void;
}) {
  return (
    <Group gap="xs" align="center">
      {/* `aria-expanded` — состояние для программы чтения с экрана и для теста: в jsdom
          анимация высоты не проигрывается. */}
      <Button
        variant="subtle"
        size="compact-sm"
        px={6}
        leftSection={
          <IconChevronDown
            size={16}
            style={{
              transform: shown ? 'rotate(180deg)' : 'none',
              transition: 'transform 200ms cubic-bezier(0.32, 0.72, 0, 1)',
            }}
          />
        }
        aria-expanded={shown}
        aria-controls={formId}
        onClick={onToggle}
      >
        Новый прогон
      </Button>
      {!shown && (
        <Text size="sm" c="dimmed">
          ключи, рынок и глубина — смета до запуска
        </Text>
      )}
    </Group>
  );
}

interface Props {
  home: SegmentHome;
  onStarted: (run: number) => void;
  /** «Взять в описание» у проверки описания при смете: предложение помощника — в правку
   *  описания сегмента (сохраняет человек). */
  onPropose: (text: string) => void;
}

/** Запуск — по смете, которую видел человек: с её отпечатком. Отказ («смета устарела») —
 *  перечитать описание сегмента: его могли поправить в соседней вкладке. */
function useStart(onStarted: (run: number) => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ sent, digest }: { sent: SegmentAsk; digest: string }) => startRun(sent, digest),
    onSuccess: (queued) => {
      notify({ message: `Прогон №${queued.run_id} ушёл в очередь задач`, color: 'green' });
      onStarted(queued.run_id);
    },
    onError: () => void client.invalidateQueries({ queryKey: SEGMENT_QUERY_KEY }),
  });
}

/** Кнопки формы: смета — всегда, запуск — с правом «run», без него — кто запускает. */
function Actions({
  counting,
  canCount,
  starting,
  ready,
  onCount,
  onStart,
}: {
  counting: boolean;
  canCount: boolean;
  starting: boolean;
  ready: boolean;
  onCount: () => void;
  onStart: () => void;
}) {
  const { can } = useSession();
  return (
    <Group gap="sm" wrap="wrap">
      <Button
        variant="default"
        className="press"
        loading={counting}
        disabled={!canCount}
        onClick={onCount}
      >
        Посчитать смету
      </Button>
      {can('run') ? (
        <Button className="press" disabled={!ready} loading={starting} onClick={onStart}>
          Запустить прогон
        </Button>
      ) : (
        <Text size="sm" c="dimmed">
          Запускает сотрудник с правом «{PERMISSION_TITLES.run}».
        </Text>
      )}
    </Group>
  );
}

/** Что не вышло — словами, под сметой: описание поправили не здесь, смета не посчитана,
 *  прогон не запущен. */
function Refusals({
  moved,
  counted,
  started,
}: {
  moved: boolean;
  counted: Error | null;
  started: Error | null;
}) {
  return (
    <>
      {moved && (
        <Alert color="yellow" title="Смета не показана">
          Описание сегмента поправили, пока вкладка была открыта: оно перечитано — посчитайте смету
          заново.
        </Alert>
      )}
      {counted !== null && (
        <Alert color="red" title="Смета не посчитана">
          {refusalOf(counted)}
        </Alert>
      )}
      {started !== null && (
        <Alert color="red" title="Прогон не запущен">
          {refusalOf(started)}
        </Alert>
      )}
    </>
  );
}

function initialDraft(home: SegmentHome): Draft {
  return {
    keywords: '',
    country: DEFAULT_COUNTRY,
    language: BY_MARKET,
    depth: 1,
    serpMode: serpDefault(home),
  };
}

export function SegmentForm({ home, onStarted, onPropose }: Props) {
  const formId = useId();
  const [draft, setDraft] = useState<Draft>(() => initialDraft(home));
  const [dropped, setDropped] = useState<Dropped[]>([]);
  // Есть прогоны — форма свёрнута, пока её не раскрыли; прогонов нет — раскрыта, кнопки нет.
  const folded = home.runs.length > 0;
  const [opened, setOpened] = useState(false);
  const excluded = excludedLines(draft.keywords, dropped);
  const now = asked(home.hypothesis_id, draft, excluded, home.segment);
  const { count, shown, fresh, moved, drop } = useEstimate(now);
  const start = useStart((run) => {
    drop();
    setOpened(false);
    onStarted(run);
  });
  const found = fresh ? shown?.found : undefined;
  const failed = count.error !== null && count.variables?.key === now.key;
  return (
    <Stack gap="sm">
      {folded ? (
        <FoldButton shown={opened} formId={formId} onToggle={() => setOpened(!opened)} />
      ) : (
        <Title order={5}>Новый прогон</Title>
      )}
      <Collapse
        id={formId}
        in={!folded || opened}
        transitionDuration={220}
        transitionTimingFunction="ease"
      >
        <Stack gap="sm">
          <Fields
            home={home}
            draft={draft}
            onDraft={(patch) => {
              start.reset();
              setDraft((was) => ({ ...was, ...patch }));
            }}
          />
          <Actions
            counting={count.isPending}
            canCount={draft.keywords.trim() !== ''}
            starting={start.isPending}
            ready={found !== undefined && found.refusal === null}
            onCount={() => count.mutate(now)}
            onStart={() => found && start.mutate({ sent: now.ask, digest: found.digest })}
          />
          {shown !== null && (
            <EstimateBlock
              found={shown.found}
              fresh={fresh}
              failed={failed}
              excluded={excluded}
              onToggle={(line, taken) => {
                start.reset();
                setDropped((was) => toggled(draft.keywords, was, line, taken));
              }}
              onPropose={onPropose}
            />
          )}
          <Refusals moved={moved} counted={failed ? count.error : null} started={start.error} />
        </Stack>
      </Collapse>
    </Stack>
  );
}
