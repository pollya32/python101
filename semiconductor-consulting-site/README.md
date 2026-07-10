# 반도체 컨설팅 사이트 (Astro)

1인 반도체 공정/수율 자문 및 학습 콘텐츠 사이트.

## 개발

```bash
npm install
npm run dev       # http://localhost:4321
```

## 빌드 / 배포

```bash
npm run build
```

빌드 결과물은 `../docs/consulting/`에 생성됩니다. 이 저장소는 GitHub Pages에서
`docs/` 폴더를 서빙하므로, 빌드 후 변경된 `docs/consulting/`을 커밋·푸시하면
`https://<user>.github.io/python101/consulting/`에 반영됩니다.

## 글 작성

`src/content/blog/`에 마크다운 파일을 추가하면 자동으로 `/blog/`에 노출됩니다.

```md
---
title: "글 제목"
description: "요약"
pubDate: 2026-01-01
tags: ["태그1", "태그2"]
draft: false
---

본문 내용...
```

## 연락처 수정

`src/pages/contact.astro`의 `email` 상수를 실제 연락처로 교체하세요.
