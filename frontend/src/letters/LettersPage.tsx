/**
 * Письма: очередь на отправку с предпросмотром.
 *
 * Единственный экран, где человек смотрит на текст до того, как тот
 * уйдёт постороннему. Отсюда всё его устройство.
 *
 * **Очередь слева, письмо справа.** Список нужен, чтобы выбирать,
 * а решение принимают по тексту — значит текст не прячется за щелчком
 * в отдельное окно. На узком окне столбцы складываются в один.
 *
 * **Отправка — по одному письму.** Кнопки «отправить всё» здесь нет
 * и не будет: она отменяет смысл экрана.
 *
 * **Что мешает отправке, сказано до нажатия, а не после.** Незаполненная
 * обязательная настройка и ненастоящий транспорт — не мелкий шрифт внизу,
 * а предупреждение рядом с кнопкой, и кнопка при первом из них
 * не нажимается.
 *
 * **Этапы не смешиваются.** Вопрос донору о цене и оффер рекламодателю
 * читаются разными глазами и уходят с разных доменов: у каждого своя
 * очередь, свой текст по умолчанию и своя воронка. У рекламодателей нет
 * прогонов — их находит обход доноров, — поэтому и выбора прогонов нет.
 *
 * **Смена этапа не перерисовывает экран.** Заголовок и переключатель стоят,
 * прежняя очередь видна приглушённой, пока не придёт новая, и не нажимается:
 * письмо одного этапа под переключателем другого — не то, что отправляют.
 * Раньше экран целиком сменялся значком загрузки и рисовался заново — «как
 * будто страница загружается заново» (замечание 25.09.2026).
 *
 * **Выбранное письмо всегда на виду.** На широком окне предпросмотр
 * закреплён рядом с очередью: письмо из середины списка открывалось в
 * четырёх с половиной тысячах пикселей выше окна. На узком предпросмотр
 * стоит над очередью, и выбор письма прокручивает к нему — а не оставляет
 * его после семидесяти семи строк списка.
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Grid,
  Group,
  Loader,
  NumberInput,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useMediaQuery, useReducedMotion } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { Dispatch, RefObject, SetStateAction } from 'react';

import { refusalOf } from '../api/client';
import { buildLetters, editLetter, listLetters, sendLetter, skipLetter } from '../api/letters';
import { mailSettingsList, settingsInWords } from '../api/labels';
import type { Corridor, LetterDraft, LetterStage, LettersView, QueuedLetter } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { Metric } from '../components/Metric';
import { formatPercent } from '../format';
import { LetterDraftEditor, draftOf, sameDraft } from './LetterDraftEditor';
import { LetterPreview, toneOf } from './LetterPreview';
import { corridorText, uniquenessText } from './letterText';
import { RunPicker } from './RunPicker';
import { JobLine } from '../jobs/JobLine';

/** Уже этого очередь и письмо стоят друг под другом (граница `md` у сетки). */
const ONE_COLUMN = '(max-width: 61.99em)';

/** Прокрутка к письму: плавно, а у того, кто просил без движения, — сразу. */
function scrollTo(calm: boolean): ScrollIntoViewOptions {
  return { behavior: calm ? 'auto' : 'smooth', block: 'start' };
}

const LETTERS_QUERY_KEY = ['letters'] as const;

/** Где экран помнит номер последней сборки — у каждого этапа свой. */
const BUILD_JOB_KEY = 'letters:last-build-job';

/** Где экран помнит выбранный этап: вернувшись, человек продолжает там же. */
const STAGE_KEY = 'letters:stage';

function jobKeyOf(stage: LetterStage): string {
  return stage === 'donors' ? BUILD_JOB_KEY : `${BUILD_JOB_KEY}:${stage}`;
}

function remembered(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function remember(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Хранилище недоступно (приватное окно) — помним до перезагрузки.
  }
}

/**
 * Ступени воронки рекламодателей по порядку и что делать, если на ступени
 * ноль. Воронка накопительная: «с адресом 0» после нуля на цене — не про
 * адреса, и совет «ищите контакты» отправил бы платить за поиск впустую.
 * Поэтому называется одна ступень — первая, где рекламодатели кончились.
 */
const ADVERTISER_STOPS: [string, string][] = [
  ['рекламодателей', 'их ещё нет — рекламодателей находит обход доноров с ценой'],
  ['со ссылкой', 'ни у кого нет найденной ссылки целиком, письмо не под что писать'],
  [
    'цена донора свежая',
    'сначала нужны ответы доноров со свежей ценой, оффер «мы дешевле» без неё не пишется',
  ],
  ['с адресом', 'пора искать контакты рекламодателей'],
  ['вне стоп-листа', 'все оставшиеся в стоп-листе'],
  ['ещё не писали', 'написаны все'],
];

function advertiserStop(funnel: Record<string, number>): string | null {
  const found = ADVERTISER_STOPS.find(([step]) => (funnel[step] ?? 0) === 0);
  return found === undefined ? null : `Кончились на ступени «${found[0]}»: ${found[1]}.`;
}

const STAGES: { value: LetterStage; label: string }[] = [
  { value: 'donors', label: 'Донорам' },
  { value: 'advertisers', label: 'Рекламодателям' },
];

/** Что экран говорит об этапе — словами человека. */
const ABOUT: Record<LetterStage, { lead: string; placeholder: string; who: string }> = {
  donors: {
    lead:
      'Очередь на отправку. Приветствие, вступление и вопрос переписаны моделью под ' +
      'конкретного донора; оффер, условия и подпись неизменны — модель их не видит вовсе.',
    placeholder: 'Май, ниша ремонта',
    who: 'донор',
  },
  advertisers: {
    lead:
      'Оффер рекламодателям под найденную ссылку: площадка, страница и анкор стоят в ' +
      'неизменяемой части письма дословно, цена донора не называется. Приветствие, вступление ' +
      'и вопрос переписаны моделью под рекламодателя.',
    placeholder: 'Сентябрь, рекламодатели ставок',
    who: 'рекламодатель',
  },
};

export function LettersPage() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const [stage, setStage] = useState<LetterStage>(() =>
    remembered(STAGE_KEY) === 'advertisers' ? 'advertisers' : 'donors',
  );
  const [chosen, setChosen] = useState<number | null>(null);
  const [campaign, setCampaign] = useState('');
  const [limit, setLimit] = useState<number>(50);
  // Сроки добивок задаются здесь, при создании рассылки: их подбирают
  // по отклику, и у рассылки, которая уже идёт, они меняться не должны.
  // Пусто — значит взять умолчание сервера.
  const [followups, setFollowups] = useState<(number | null)[]>([null, null]);
  // Правка текста письма. `null` — не трогали: тогда на сервер ничего
  // не уходит, и одноимённая рассылка дополняется своим текстом.
  const [letterEdit, setLetterEdit] = useState<LetterDraft | null>(null);
  // Прогоны рассылки: письма получают только принятые доноры выбранных.
  const [runIds, setRunIds] = useState<number[]>([]);
  // Номер последней сборки переживает перезагрузку страницы: сборка идёт
  // минутами, и человек, вернувшийся к экрану, должен увидеть, чем кончилась.
  const [buildJobs, setBuildJobs] = useState<Record<LetterStage, string | null>>(() => ({
    donors: remembered(jobKeyOf('donors')),
    advertisers: remembered(jobKeyOf('advertisers')),
  }));
  const buildJob = buildJobs[stage];
  const oneColumn = useMediaQuery(ONE_COLUMN) === true;
  const calm = useReducedMotion();
  const previewRef = useRef<HTMLDivElement>(null);
  // Выбор письма на узком окне прокручивает к нему — один раз, после того
  // как выбранное письмо отрисовано, а не на каждую перерисовку.
  const reveal = useRef(false);

  const query = useQuery({
    queryKey: [...LETTERS_QUERY_KEY, stage],
    queryFn: () => listLetters(stage),
    // Пока идёт очередь другого этапа, стоит прежняя — приглушённой. Без
    // этого экран целиком менялся на значок загрузки и рисовался заново.
    placeholderData: keepPreviousData,
  });
  const { data, error } = query;
  const stale = query.isPlaceholderData;

  // Правка текста, выбранные прогоны и письмо — свои у каждого этапа:
  // текст вопроса донору, уехавший в оффер рекламодателю, сервер
  // не примет, а человек не поймёт, откуда он взялся.
  const switchStage = (next: LetterStage) => {
    setStage(next);
    setChosen(null);
    setLetterEdit(null);
    setRunIds([]);
    remember(STAGE_KEY, next);
  };

  // `data?.letters ?? []` в теле создаёт новый массив на каждую отрисовку,
  // и наблюдатель за очередью срабатывал бы всегда. Пустой список — константа.
  const letters = useMemo(() => data?.letters ?? [], [data]);
  const defaultDays = useMemo(() => data?.followup_default ?? [], [data]);
  const letterDefault = data?.letter_default ?? null;
  const letterChanged =
    letterEdit !== null && letterDefault !== null && !sameDraft(letterEdit, draftOf(letterDefault));
  const selected = letters.find((letter) => letter.id === chosen) ?? letters[0] ?? null;

  // Выбранное письмо могло уйти из очереди — отправили, пропустили.
  // Тогда выбор снимается, иначе экран показывает письмо, которого
  // в очереди уже нет.
  useEffect(() => {
    if (chosen !== null && !letters.some((letter) => letter.id === chosen)) setChosen(null);
  }, [chosen, letters]);

  useEffect(() => {
    if (!reveal.current) return;
    reveal.current = false;
    previewRef.current?.scrollIntoView(scrollTo(calm));
  }, [selected?.id, calm]);

  const choose = (id: number) => {
    // Рядом с очередью письмо и так на виду; в одну колонку оно над
    // списком, и без прокрутки выбор выглядел бы как «ничего не произошло».
    if (oneColumn && id === selected?.id) {
      previewRef.current?.scrollIntoView(scrollTo(calm));
      return;
    }
    reveal.current = oneColumn;
    setChosen(id);
  };

  const refresh = () => queryClient.invalidateQueries({ queryKey: LETTERS_QUERY_KEY });

  const build = useMutation({
    mutationFn: () =>
      buildLetters({
        campaign: campaign.trim(),
        stage,
        limit,
        followup_days: followups.map((days, index) => days ?? defaultDays[index] ?? 0),
        ...(letterChanged && letterEdit !== null ? { letter: letterEdit } : {}),
        // У рекламодателей прогонов нет: сервер откажет, если их прислать.
        ...(stage === 'donors' ? { run_ids: runIds } : {}),
      }),
    onSuccess: async (queued) => {
      setBuildJobs((was) => ({ ...was, [stage]: queued.job_id }));
      remember(jobKeyOf(stage), queued.job_id);
      await refresh();
      notifications.show({
        message: 'Сборка ушла в очередь задач: каждое письмо стоит вызова модели, это минуты',
        color: 'green',
      });
    },
    onError: (failure) =>
      notifications.show({
        title: 'Не собрали',
        message: settingsInWords(refusalOf(failure)),
        color: 'red',
      }),
  });

  const send = useMutation({
    mutationFn: (id: number) => sendLetter(id),
    onSuccess: async (result) => {
      await refresh();
      notifications.show({
        message: result.real
          ? `Письмо ушло с ящика ${result.sender_email}`
          : `Письмо помечено отправленным, но наружу НЕ ушло: транспорт не настроен`,
        color: result.real ? 'green' : 'yellow',
      });
    },
    onError: (failure) =>
      notifications.show({
        title: 'Не отправили',
        message: settingsInWords(refusalOf(failure)),
        color: 'red',
      }),
  });

  const skip = useMutation({
    mutationFn: (id: number) => skipLetter(id),
    onSuccess: async () => {
      await refresh();
      notifications.show({
        message: `Письмо убрано из очереди, этот ${ABOUT[stage].who} в следующей сборке не появится`,
        color: 'yellow',
      });
    },
    onError: (failure) =>
      notifications.show({
        title: 'Не убрали',
        message: settingsInWords(refusalOf(failure)),
        color: 'red',
      }),
  });

  const save = useMutation({
    mutationFn: ({ id, subject, body }: { id: number; subject: string; body: string }) =>
      editLetter(id, { subject, body }),
    onSuccess: async (letter) => {
      await refresh();
      notifications.show({
        message: `Сохранено, отличие от шаблона — ${
          data === undefined
            ? formatPercent(letter.uniqueness)
            : uniquenessText(letter.uniqueness, data.corridor)
        }`,
        color: letter.verdict === null ? 'green' : 'yellow',
      });
    },
    onError: (failure) =>
      notifications.show({
        title: 'Не сохранили',
        message: settingsInWords(refusalOf(failure)),
        color: 'red',
      }),
  });

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          {/* Заголовок и переключатель стоят при любой загрузке и любом
              отказе: без переключателя с этапа, который не загрузился,
              было бы не уйти. */}
          <Stack gap={6}>
            <Title order={3}>Письма</Title>
            <SegmentedControl
              aria-label="Кому письма"
              value={stage}
              onChange={(value) => switchStage(value as LetterStage)}
              data={STAGES}
              style={{ alignSelf: 'flex-start' }}
            />
            <Text size="sm" c="dimmed" maw={680}>
              {ABOUT[stage].lead}
            </Text>
          </Stack>

          {data === undefined && error === null ? (
            <Loader size="sm" aria-label="Загружаем очередь писем" />
          ) : null}
          {data === undefined && error !== null ? (
            <Alert color="red" title="Очередь не загрузилась">
              {refusalOf(error)}
            </Alert>
          ) : null}

          {data !== undefined ? (
            <QueueControls
              view={data}
              stage={stage}
              stale={stale}
              canSend={can('send')}
              letters={letters}
              campaign={campaign}
              onCampaign={setCampaign}
              limit={limit}
              onLimit={setLimit}
              followups={followups}
              onFollowups={setFollowups}
              building={build.isPending}
              canBuild={campaign.trim() !== '' && (stage !== 'donors' || runIds.length > 0)}
              onBuild={() => build.mutate()}
              buildJob={buildJob}
              onBuildFinished={() => void refresh()}
              runIds={runIds}
              onRunIds={setRunIds}
              letterEdit={letterEdit}
              onLetterEdit={setLetterEdit}
            />
          ) : null}
        </Stack>
      </Card>

      {data !== undefined ? (
        <Queue
          view={data}
          stage={stage}
          stale={stale}
          letters={letters}
          selected={selected}
          previewRef={previewRef}
          canSend={can('send')}
          busy={send.isPending || skip.isPending || save.isPending}
          onChoose={choose}
          onSend={(id) => send.mutate(id)}
          onSkip={(id) => skip.mutate(id)}
          onSave={(id, subject, body) => save.mutate({ id, subject, body })}
        />
      ) : null}
    </Stack>
  );
}

interface ControlsProps {
  view: LettersView;
  stage: LetterStage;
  /** Показана очередь прежнего этапа, пока идёт новая. */
  stale: boolean;
  canSend: boolean;
  letters: QueuedLetter[];
  campaign: string;
  onCampaign: (value: string) => void;
  limit: number;
  onLimit: (value: number) => void;
  followups: (number | null)[];
  onFollowups: Dispatch<SetStateAction<(number | null)[]>>;
  building: boolean;
  canBuild: boolean;
  onBuild: () => void;
  buildJob: string | null;
  onBuildFinished: () => void;
  runIds: number[];
  onRunIds: (next: number[]) => void;
  letterEdit: LetterDraft | null;
  onLetterEdit: (next: LetterDraft) => void;
}

/** Сводка этапа, препятствия отправке и сборка очереди. */
function QueueControls({
  view,
  stage,
  stale,
  canSend,
  letters,
  campaign,
  onCampaign,
  limit,
  onLimit,
  followups,
  onFollowups,
  building,
  canBuild,
  onBuild,
  buildJob,
  onBuildFinished,
  runIds,
  onRunIds,
  letterEdit,
  onLetterEdit,
}: ControlsProps) {
  const offCorridor = letters.filter((letter) => letter.verdict !== null).length;
  const defaultDays = view.followup_default;
  const letterDefault = view.letter_default;

  return (
    // Прежний этап — приглушён и не нажимается: текст первого письма
    // донорам, поправленный под переключателем «Рекламодателям», сервер
    // бы не принял, а человек не понял бы, откуда он взялся.
    <Stack gap="md" className="staleRows" data-stale={stale || undefined} inert={stale}>
      {/* Плитки — прямые дети сетки: так они подсетка её ряда, и числа стоят
          на одной линии. В колонках `Grid` подсетки не было — у «В очереди»
          без пояснения подпись и число сидели на 10 px ниже соседних (замер
          26.09.2026), на телефоне так же стояли «Ещё не писали» и «Почта». */}
      <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
        <Metric title="В очереди" value={letters.length} />
        <Metric
          title="Вне коридора"
          value={offCorridor}
          hint={`коридор ${corridorText(view.corridor)}`}
          color={offCorridor > 0 ? 'yellow' : undefined}
        />
        <Metric
          title="Ещё не писали"
          value={view.funnel['ещё не писали'] ?? 0}
          hint={
            stage === 'donors'
              ? `подходящих ${view.funnel['подходящих'] ?? 0}`
              : `рекламодателей ${view.funnel['рекламодателей'] ?? 0}`
          }
        />
        <Metric
          title="Почта"
          value={
            view.transport.real ? (
              view.transport.name
            ) : (
              // Слова переносятся по слогам: на телефоне «подключена» шире
              // плитки и вылезала за её край (на 17 px при 390, аудит 25.09).
              <span className="tileWords">не подключена</span>
            )
          }
          hint={view.transport.real ? 'письма уходят' : 'подключается на рабочем сервере'}
          color={view.transport.real ? undefined : 'yellow'}
        />
      </SimpleGrid>

      {view.blocked_by.length > 0 ? (
        <Alert color="yellow" title="Отправка пока не подключена">
          Не задано: {mailSettingsList(view.blocked_by)} — настраивается при подключении почты.
          Очередь собирается и видна, письма можно читать и править; отправить их получится после
          подключения.
        </Alert>
      ) : null}

      {view.transport.problem !== null ? (
        // Текст отказа транспорта пишется для журнала и называет переменные
        // окружения; на экране — словами (`settingsInWords`).
        <Alert color="yellow" title="Почта не подключилась">
          {settingsInWords(view.transport.problem)}
        </Alert>
      ) : null}

      {canSend ? (
        // `fieldRow` резервирует место под пояснение: «Кампания» с пояснением
        // в две строки стояла на 14 px выше «Писем за раз» (аудит 25.09.2026).
        <Group align="flex-end" gap="sm" className="fieldRow">
          <TextInput
            label="Кампания"
            description="Одноимённая дополняется, а не заводится второй раз"
            placeholder={ABOUT[stage].placeholder}
            value={campaign}
            w={280}
            onChange={(event) => onCampaign(event.currentTarget.value)}
          />
          <NumberInput
            label="Писем за раз"
            description="Каждое стоит вызова модели"
            value={limit}
            min={1}
            max={500}
            w={180}
            onChange={(value) => onLimit(typeof value === 'number' ? value : 50)}
          />
          {defaultDays.map((fallback, index) => (
            <NumberInput
              key={index}
              label={`Добивка ${index + 1}, дней`}
              description={index === 0 ? 'после первого письма' : 'после предыдущей'}
              value={followups[index] ?? fallback}
              min={0}
              max={90}
              w={150}
              onChange={(value) =>
                onFollowups((was) =>
                  was.map((old, at) =>
                    at === index ? (typeof value === 'number' ? value : null) : old,
                  ),
                )
              }
            />
          ))}
          <Button
            color="lagoon"
            className="press"
            loading={building}
            disabled={!canBuild}
            onClick={onBuild}
          >
            Собрать очередь
          </Button>
        </Group>
      ) : null}

      {buildJob !== null ? <JobLine jobId={buildJob} onFinished={onBuildFinished} /> : null}

      {canSend && stage === 'donors' ? <RunPicker value={runIds} onChange={onRunIds} /> : null}

      {canSend ? (
        <LetterDraftEditor
          key={stage}
          stage={stage}
          fallback={letterDefault}
          value={letterEdit ?? draftOf(letterDefault)}
          onChange={onLetterEdit}
        />
      ) : null}
    </Stack>
  );
}

interface QueueProps {
  view: LettersView;
  stage: LetterStage;
  stale: boolean;
  letters: QueuedLetter[];
  selected: QueuedLetter | null;
  previewRef: RefObject<HTMLDivElement | null>;
  canSend: boolean;
  busy: boolean;
  onChoose: (id: number) => void;
  onSend: (id: number) => void;
  onSkip: (id: number) => void;
  onSave: (id: number, subject: string, body: string) => void;
}

/** Очередь и выбранное письмо — или объяснение, почему очередь пуста. */
function Queue({
  view,
  stage,
  stale,
  letters,
  selected,
  previewRef,
  canSend,
  busy,
  onChoose,
  onSend,
  onSkip,
  onSave,
}: QueueProps) {
  if (letters.length === 0) {
    return (
      <Card className="glass staleRows" p="xl" data-stale={stale || undefined}>
        <Stack gap="xs">
          <Text fw={500}>Очередь пуста</Text>
          <Text size="sm" c="dimmed">
            {stage === 'donors' ? (
              <>
                Подходящих доноров {view.funnel['подходящих'] ?? 0}, из них с адресом{' '}
                {view.funnel['с адресом'] ?? 0}, и ещё не писали {view.funnel['ещё не писали'] ?? 0}
                . Если последнее число ноль — написаны все; если ноль второе — пора добрать
                контакты.
              </>
            ) : (
              <>
                Рекламодателей {view.funnel['рекламодателей'] ?? 0}, из них с найденной ссылкой{' '}
                {view.funnel['со ссылкой'] ?? 0}, со свежей ценой донора{' '}
                {view.funnel['цена донора свежая'] ?? 0}, с адресом {view.funnel['с адресом'] ?? 0},
                и ещё не писали {view.funnel['ещё не писали'] ?? 0}. {advertiserStop(view.funnel)}
              </>
            )}
          </Text>
        </Stack>
      </Card>
    );
  }

  return (
    <Grid
      gutter="lg"
      align="flex-start"
      className="staleRows"
      data-stale={stale || undefined}
      inert={stale}
      aria-busy={stale || undefined}
    >
      {/* На узком окне письмо — над очередью: иначе оно стояло после всех
          строк списка, и выбранное приходилось искать прокруткой. */}
      <Grid.Col span={{ base: 12, md: 5 }} order={{ base: 2, md: 1 }}>
        <Card className="glass" p="xs">
          <Stack gap={4}>
            {letters.map((letter) => (
              <LetterRow
                key={letter.id}
                letter={letter}
                corridor={view.corridor}
                active={selected?.id === letter.id}
                onChoose={() => onChoose(letter.id)}
              />
            ))}
          </Stack>
        </Card>
      </Grid.Col>
      <Grid.Col
        span={{ base: 12, md: 7 }}
        order={{ base: 1, md: 2 }}
        className="letterPreviewCol"
        ref={previewRef}
      >
        {selected !== null ? (
          <LetterPreview
            letter={selected}
            corridor={view.corridor}
            blockedBy={view.blocked_by}
            transportIsReal={view.transport.real}
            canSend={canSend}
            busy={busy}
            onSend={() => onSend(selected.id)}
            onSkip={() => onSkip(selected.id)}
            onSave={(subject, body) => onSave(selected.id, subject, body)}
          />
        ) : null}
      </Grid.Col>
    </Grid>
  );
}

interface RowProps {
  letter: QueuedLetter;
  corridor: Corridor;
  active: boolean;
  onChoose: () => void;
}

/** Строка очереди. Щёлкают по ней целиком — и с клавиатуры тоже: до этого
 *  выбрать письмо без мыши было нельзя вовсе. */
function LetterRow({ letter, corridor, active, onChoose }: RowProps) {
  return (
    <Card
      className={active ? 'glassQuiet press' : 'glassSlot press liftable'}
      p="sm"
      role="button"
      tabIndex={0}
      style={{ cursor: 'pointer' }}
      onClick={onChoose}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onChoose();
        }
      }}
      aria-current={active ? 'true' : undefined}
    >
      <Group justify="space-between" wrap="nowrap" gap="sm">
        <Stack gap={2} style={{ minWidth: 0 }}>
          <Text fw={active ? 600 : 500} truncate>
            {letter.host}
          </Text>
          <Text size="xs" c="dimmed" truncate>
            {letter.email ?? 'адрес не определён'}
          </Text>
        </Stack>
        <Badge variant="light" color={toneOf(letter)}>
          {uniquenessText(letter.uniqueness, corridor)}
        </Badge>
      </Group>
    </Card>
  );
}
