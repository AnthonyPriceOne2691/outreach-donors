/**
 * Поле гипотезы «Вид сайта» в окне «Качество сайтов» плана: какие сайты судья берёт — сайт самой
 * компании, каталог, издание, «не компания» (виды судьи, `hypothesis_kinds` на сервере; своих видов
 * здесь нет).
 *
 * Пусто — как до поля: какие виды подходят, решает описание сегмента. Выбрано — сайт другого вида
 * судья отклоняет, и сайт самой компании тоже; судья в наблюдении такой сайт не отсекает — он в
 * «Судья отказал бы»; сайт, по которому вид не понять, решает человек. Собранное правка сама не
 * пересуживает — пересуд прогона ставит полосы заново по фактам из кэша, без модели, как у стран
 * (стенд 20.11). Поле — в карточке гипотезы с сервера, а в общем типе карточки (`api/salesTypes.ts`)
 * его нет: чтение — здесь, как у поля «страны компаний» (`hypothesisData.countriesOf`). Флажки, а не
 * список: видов четыре, и все видны сразу.
 *
 * **Имя группы — словами подписи** (`aria-label`), а подпись с кнопкой «i» — над группой, как у
 * «Компании из других стран»: подпись `Checkbox.Group` Mantine отдаёт группе целиком, и читалка
 * называла её «Вид сайта Что значит «Вид сайта»» (стенд 20.11; правило `HintLabel`).
 */

import { Checkbox, Stack, Text } from '@mantine/core';

import type { HypothesisCard } from '../api/salesTypes';
import { HintLabel } from '../components/HintLabel';
import type { SiteKind } from './planApi';

const LABEL = 'Вид сайта';
const NOTE = 'Пусто — какие сайты подходят, решает описание сегмента';
const HINT =
  'Выбрали виды — сайт другого вида судья отклоняет, и сайт самой компании тоже; судья в наблюдении такой сайт не отсекает — он в «Судья отказал бы». Сайт, по которому вид не понять, по-прежнему решает человек. Поправили — пересудите прогон: полосы встанут заново по уже собранным фактам, без модели.';

/** Виды по порядку сервера (`hypothesis_kinds.SITE_KINDS`) — подписями для человека. */
export const SITE_KIND_CHOICES: { value: SiteKind; label: string }[] = [
  { value: 'own', label: 'Сайт самой компании' },
  { value: 'intermediary', label: 'Каталог, биржа, площадка' },
  { value: 'media', label: 'Издание или блог' },
  { value: 'other', label: 'Не компания: госорган, школа, ассоциация' },
];

const ORDER = SITE_KIND_CHOICES.map((choice) => choice.value);

/** Выбор по порядку видов, без чужих значений, — так его хранит сервер. */
function ordered(kinds: readonly string[]): SiteKind[] {
  return ORDER.filter((kind) => kinds.includes(kind));
}

/** Поле карточки; сервер его не прислал — пусто (как до поля). */
export function siteKindsOf(card: HypothesisCard | undefined): SiteKind[] {
  const found = card as (HypothesisCard & { site_kinds?: string[] }) | undefined;
  return ordered(found?.site_kinds ?? []);
}

/** Выбор поправили — уходит целиком (пустой — снять выбор); нет — ничего. */
export function kindsChange(was: SiteKind[], now: SiteKind[]): { site_kinds?: SiteKind[] } {
  const picked = ordered(now);
  return picked.join() === ordered(was).join() ? {} : { site_kinds: picked };
}

interface Props {
  value: SiteKind[];
  onChange: (kinds: SiteKind[]) => void;
}

export function SiteKinds({ value, onChange }: Props) {
  const toggle = (kind: SiteKind, on: boolean) =>
    onChange(ordered(on ? [...value, kind] : value.filter((chosen) => chosen !== kind)));
  return (
    <Stack gap={4}>
      <Text component="div" size="sm" fw={500}>
        <HintLabel label={LABEL} hint={HINT} />
      </Text>
      <Text size="xs" c="dimmed">
        {NOTE}
      </Text>
      <Stack role="group" aria-label={LABEL} gap={6} mt={2}>
        {SITE_KIND_CHOICES.map((choice) => (
          <Checkbox
            key={choice.value}
            label={choice.label}
            checked={value.includes(choice.value)}
            onChange={(event) => toggle(choice.value, event.currentTarget.checked)}
          />
        ))}
      </Stack>
    </Stack>
  );
}
