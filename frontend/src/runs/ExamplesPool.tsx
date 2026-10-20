/**
 * «Собрать моделью» по примерам сайтов: человек показывает сайты, которые ему нужны, —
 * сервис открывает их главные, модель называет общую тему, и тот же генератор, что
 * у сборки по теме, собирает ключи (`keywords/by_examples.py`).
 *
 * **Своим компонентом, а не строками экрана прогона**: поле, запрос и итог живут здесь,
 * экран только переключает основу сборки и отдаёт ключи в своё поле.
 *
 * **Ключи падают в то же поле, что у сборки по теме**: человек видит их и правит до
 * сметы, а смета и кап остаются последним рубежом перед тратой.
 *
 * **Итог называет, на чём стоит тема**: сама тема, тема ниши на языке рынка, каждый
 * пример с исходом (прочитан, не про общую тему, не прочитан — словами сервера) и
 * токены модели. Тема по двум примерам из пяти — не то же, что по пяти, и человек
 * должен это видеть до сметы.
 */

import { Stack, Text, Textarea } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { buildFromExamples } from '../api/examples';
import type { ExamplesPool, ExamplesRequest, ExampleSite } from '../api/examples';
import { languageTitle } from '../api/languageLabels';
import { formatNumber, plural } from '../format';
import { notify, refuse, settle } from '../notices';
import { countExamples, examplesCaption } from './examples';
import { keywordsTitle } from './depth';

/** Основа сборки моделью: тема, вписанная человеком, или примеры сайтов. */
export type PoolBasis = 'topic' | 'examples';

export const POOL_BASES: { label: string; value: PoolBasis }[] = [
  { label: 'По теме', value: 'topic' },
  { label: 'По примерам сайтов', value: 'examples' },
];

interface FieldProps {
  value: string;
  onChange: (value: string) => void;
  /** Почему поле нельзя отправить — словами сервера. */
  refusal: string | null;
}

/** Поле примеров: по адресу в строке; сколько сайтов и повторов — под полем, пока
 *  отказа нет: отказ сам говорит, что не так. */
export function ExamplesField({ value, onChange, refusal }: FieldProps) {
  const count = countExamples(value);
  return (
    // Счёт — в 8 px под полем и шириной по тексту: в 4 px он лип к кромке поля, и замер
    // контраста брал кромку в фокусе вместо букв (3,56 : 1 у текста с 11,5 : 1).
    <Stack gap={8}>
      <Textarea
        label="Примеры сайтов"
        description="Адрес или домен, по одному в строке. Сервис откроет их главные, модель назовёт общую тему и соберёт ключи на языке рынка"
        placeholder={'example.com\nhttps://example.org/blog'}
        autosize
        minRows={3}
        maxRows={10}
        value={value}
        error={refusal}
        onChange={(event) => onChange(event.currentTarget.value)}
      />
      {refusal === null && count.sites > 0 ? (
        <Text size="xs" c="dimmed" w="fit-content">
          {examplesCaption(count)}
        </Text>
      ) : null}
    </Stack>
  );
}

/** Сборка по примерам: запрос, уведомление и ключи — в поле экрана. */
export function useExamplesBuild(onBuilt: (built: ExamplesPool) => void) {
  return useMutation({
    mutationFn: (asked: ExamplesRequest) => buildFromExamples(asked),
    onSuccess: (built) => {
      settle('run-examples');
      onBuilt(built);
      const unread = built.sites.filter((site) => !site.read).length;
      notify({
        title: `Собрано ${keywordsTitle(built.keywords.length)} по примерам`,
        message:
          unread > 0
            ? `Тема — по ${built.sites.length - unread} из ${built.sites.length} примеров: остальные не прочитаны. Проверьте список перед сметой.`
            : `Тема: ${built.theme}. Проверьте список перед сметой.`,
        color: unread > 0 || built.refusals.length > 0 ? 'yellow' : 'green',
      });
    },
    onError: (failure) =>
      refuse('run-examples', {
        title: 'Ключи по примерам не собрались',
        message: refusalOf(failure),
      }),
  });
}

/** Исход примера словами: прочитан, не про общую тему или почему не прочитан. */
function siteOutcome(site: ExampleSite): string {
  if (!site.read) return `не прочитан: ${site.reason}`;
  if (site.off_theme) return 'прочитан, но не про общую тему — тему не задаёт';
  return site.title === '' ? 'прочитан' : `прочитан: «${site.title}»`;
}

/** Темы ниши по языкам рынка — ровно то, что ушло генератору: «home gardening»
 *  (английский), «jardinage» (французский). */
function topicsLine(topics: Record<string, string>): string {
  return Object.entries(topics)
    .map(([language, topic]) => `«${topic}» (${languageTitle(language)})`)
    .join(', ');
}

interface OutcomeProps {
  built: ExamplesPool | undefined;
  pending: boolean;
  /** Сколько строк поля — для подсказки, пока идёт сборка. */
  sites: number;
}

/** Итог сборки по примерам — под рядом полей, пока основа сборки — примеры. */
export function ExamplesOutcome({ built, pending, sites }: OutcomeProps) {
  if (pending) {
    return (
      <Text size="sm" c="dimmed" aria-live="polite">
        Открываем главные {sites} {plural(sites, 'примера', 'примеров', 'примеров')} и спрашиваем
        модель о теме — это до пары минут.
      </Text>
    );
  }
  if (built === undefined) return null;
  // Тему задают прочитанные примеры про общую тему: не про неё — прочитан, но не в счёт.
  const shaping = built.sites.filter((site) => site.read && !site.off_theme).length;
  return (
    <Stack gap={4} aria-label="Итог сборки по примерам">
      <Text size="sm" fw={600}>
        Тема примеров: {built.theme}
      </Text>
      <Text size="sm" c="dimmed">
        Ключи — про {topicsLine(built.topics)}. Тема — по {shaping} из {built.sites.length}{' '}
        {plural(built.sites.length, 'примера', 'примеров', 'примеров')}
        {built.repeats > 0
          ? ` · ${built.repeats} ${plural(built.repeats, 'повтор', 'повтора', 'повторов')} не в счёт`
          : ''}
        .
      </Text>
      {/* Список без маркеров и отступа браузера: строка начинается с той же кромки,
          что тема над ним. */}
      <Stack gap={2} component="ul" m={0} p={0} style={{ listStyle: 'none' }}>
        {built.sites.map((site) => (
          <Text key={site.host} size="sm" component="li">
            <Text span fw={500}>
              {site.host}
            </Text>{' '}
            — {siteOutcome(site)}
          </Text>
        ))}
      </Stack>
      <Text size="sm" c="dimmed">
        Модель {built.model}: {formatNumber(built.tokens)}{' '}
        {plural(built.tokens, 'токен', 'токена', 'токенов')}, из них на тему —{' '}
        {formatNumber(built.theme_tokens)}.
      </Text>
    </Stack>
  );
}
