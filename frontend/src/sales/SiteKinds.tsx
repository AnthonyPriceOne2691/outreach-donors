/**
 * Поле гипотезы «Вид сайта» в окне «Качество сайтов» плана: какие сайты судья берёт — сайт самой
 * компании, каталог, издание, «не компания» (виды судьи, `hypothesis_kinds` на сервере; своих видов
 * здесь нет).
 *
 * Пусто — как до поля: какие виды подходят, решает описание сегмента. Выбрано — сайт другого вида
 * судья отклоняет, и сайт самой компании тоже; сайт, по которому вид не понять, решает человек.
 * Действует на новые суды: собранное не пересуживается. Поле — в карточке гипотезы с сервера, а в
 * общем типе карточки (`api/salesTypes.ts`) его нет: чтение — здесь, как у поля «страны компаний»
 * (`hypothesisData.countriesOf`). Флажки, а не список: видов четыре, и все видны сразу.
 */

import { Checkbox, Stack } from '@mantine/core';

import type { HypothesisCard } from '../api/salesTypes';
import { HintLabel } from '../components/HintLabel';
import type { SiteKind } from './planApi';

const LABEL = 'Вид сайта';
const NOTE = 'Пусто — какие сайты подходят, решает описание сегмента';
const HINT =
  'Выбрали виды — сайт другого вида судья отклоняет, и сайт самой компании тоже. Сайт, по которому вид не понять, по-прежнему решает человек. Действует на новые суды: собранные сайты не пересуживаются.';

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
  return (
    <Checkbox.Group
      label={<HintLabel label={LABEL} hint={HINT} />}
      description={NOTE}
      value={value}
      onChange={(picked) => onChange(ordered(picked))}
    >
      <Stack gap={6} mt={6}>
        {SITE_KIND_CHOICES.map((choice) => (
          <Checkbox key={choice.value} value={choice.value} label={choice.label} />
        ))}
      </Stack>
    </Checkbox.Group>
  );
}
